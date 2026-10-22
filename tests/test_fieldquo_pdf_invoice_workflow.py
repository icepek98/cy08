import json
import unittest
from datetime import date
from decimal import Decimal

from fieldquo.invoice_interpreter import InvoiceInterpretationError
from fieldquo.ollama_invoice_interpreter import OllamaInvoiceInterpreter
from fieldquo.pdf_invoice_parser import parse_fieldquo_invoice_text
from fieldquo.pdf_invoice_workflow import (
    PDFInvoiceWorkflow,
    PDFInvoiceWorkflowError,
)


COMPLETE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Due: 2025-02-01
Currency: EUR
Subtotal: 150.00
Tax: 30.00
Discount: 5.00
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""
INCOMPLETE_TEXT = """\
Invoice ref: INV-100
Client: Example Client
Issued: 2025-01-02
Due: 2025-02-01
Currency: EUR
Subtotal: 150.00
Tax: 30.00
Discount: 5.00
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
Memo: Different Client
"""


def valid_ai_response():
    response = {
        "schema_version": "1",
        "invoice": {
            "invoice_number": "INV-100",
            "client": "Example Client",
            "issued": "2025-01-02",
            "due": "2025-02-01",
            "currency": "EUR",
            "subtotal": "150.00",
            "tax": "30.00",
            "discount": "5.00",
            "total": "175.00",
            "line_items": [
                {
                    "description": "Consulting",
                    "quantity": "2",
                    "unit_price": "75.00",
                    "line_total": "150.00",
                }
            ],
        },
        "evidence": {
            "invoice_number": "INV-100",
            "client": "Example Client",
            "issued": "2025-01-02",
            "due": "2025-02-01",
            "currency": "EUR",
            "subtotal": "150.00",
            "tax": "30.00",
            "discount": "5.00",
            "total": "175.00",
            "line_items[0].description": "Consulting",
            "line_items[0].quantity": "2",
            "line_items[0].unit_price": "75.00",
            "line_items[0].line_total": "150.00",
        },
    }
    return json.dumps(response)


class FakeInterpreter:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def interpret(self, request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeTransport:
    def __init__(self, generated_response):
        self.generated_response = generated_response
        self.calls = []

    def post_json(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        return {"response": self.generated_response}


class PDFInvoiceWorkflowTests(unittest.TestCase):
    def test_complete_deterministic_invoice_skips_ollama(self):
        interpreter = FakeInterpreter(valid_ai_response())
        workflow = PDFInvoiceWorkflow(interpreter=interpreter, fallback_enabled=True)

        result = workflow.process_text(COMPLETE_TEXT)

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.invoice["invoice_number"], "INV-100")
        self.assertEqual(result.invoice["total"], Decimal("175.00"))
        self.assertEqual(interpreter.requests, [])
        self.assertTrue(result.parser_result.complete)
        self.assertEqual(result.origin, "fieldquo_pdf")

    def test_deterministic_result_wins_even_when_interpreter_is_injected(self):
        interpreter = FakeInterpreter(InvoiceInterpretationError("must not run"))
        result = PDFInvoiceWorkflow(
            interpreter=interpreter,
            fallback_enabled=True,
        ).process_text(COMPLETE_TEXT)

        self.assertEqual(len(interpreter.requests), 0)
        self.assertEqual(result.invoice["issued"], date(2025, 1, 2))

    def test_incomplete_invoice_with_fallback_disabled_is_rejected(self):
        interpreter = FakeInterpreter(valid_ai_response())
        workflow = PDFInvoiceWorkflow(interpreter=interpreter)

        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "desabilitado"):
            workflow.process_text(INCOMPLETE_TEXT)

        self.assertEqual(interpreter.requests, [])

    def test_fallback_validates_merges_and_returns_normalized_invoice(self):
        interpreter = FakeInterpreter(valid_ai_response())
        result = PDFInvoiceWorkflow(
            interpreter=interpreter,
            fallback_enabled=True,
        ).process_text(INCOMPLETE_TEXT)

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.invoice["invoice_number"], "INV-100")
        self.assertEqual(result.invoice["issued"], date(2025, 1, 2))
        self.assertEqual(result.invoice["total"], Decimal("175.00"))
        self.assertEqual(result.invoice["line_items"][0]["quantity"], Decimal("2"))
        self.assertEqual(len(interpreter.requests), 1)
        self.assertEqual(interpreter.requests[0].source_text, INCOMPLETE_TEXT)
        self.assertEqual(result.origin, "fieldquo_pdf")

    def test_invalid_interpreter_response_is_rejected(self):
        interpreter = FakeInterpreter("{invalid-json")
        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "JSON válido"):
            PDFInvoiceWorkflow(
                interpreter=interpreter,
                fallback_enabled=True,
            ).process_text(INCOMPLETE_TEXT)

    def test_field_without_evidence_is_rejected(self):
        response = json.loads(valid_ai_response())
        del response["evidence"]["invoice_number"]
        interpreter = FakeInterpreter(json.dumps(response))

        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "evidence"):
            PDFInvoiceWorkflow(
                interpreter=interpreter,
                fallback_enabled=True,
            ).process_text(INCOMPLETE_TEXT)

    def test_deterministic_value_conflict_is_rejected(self):
        conflicting_text = INCOMPLETE_TEXT
        response = json.loads(valid_ai_response())
        response["invoice"]["client"] = "Different Client"
        response["evidence"]["client"] = "Different Client"
        conflicting_interpreter = FakeInterpreter(json.dumps(response))
        workflow = PDFInvoiceWorkflow(
            interpreter=conflicting_interpreter,
            fallback_enabled=True,
        )

        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "conflita"):
            workflow.process_text(conflicting_text)

        self.assertEqual(
            conflicting_interpreter.requests[0].deterministic_invoice["client"],
            "Example Client",
        )

    def test_parser_value_error_uses_enabled_fallback_with_empty_deterministic_data(self):
        interpreter = FakeInterpreter(valid_ai_response())

        def raising_parser(_text):
            raise ValueError("controlled parser error")

        result = PDFInvoiceWorkflow(
            parser=raising_parser,
            interpreter=interpreter,
            fallback_enabled=True,
        ).process_text(INCOMPLETE_TEXT)

        self.assertTrue(result.fallback_used)
        self.assertIsNone(result.parser_result)
        self.assertTrue(
            all(value is None for value in interpreter.requests[0].deterministic_invoice.values())
        )

    def test_parser_error_does_not_trigger_fallback_when_disabled(self):
        interpreter = FakeInterpreter(valid_ai_response())

        def raising_parser(_text):
            raise ValueError("controlled parser error")

        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "Parser determinístico falhou"):
            PDFInvoiceWorkflow(parser=raising_parser, interpreter=interpreter).process_text(
                INCOMPLETE_TEXT
            )

        self.assertEqual(interpreter.requests, [])

    def test_invalid_or_incomplete_ai_result_is_rejected(self):
        response = json.loads(valid_ai_response())
        response["invoice"]["invoice_number"] = None
        del response["evidence"]["invoice_number"]
        interpreter = FakeInterpreter(json.dumps(response))

        with self.assertRaisesRegex(PDFInvoiceWorkflowError, "continua incompleta"):
            PDFInvoiceWorkflow(
                interpreter=interpreter,
                fallback_enabled=True,
            ).process_text(INCOMPLETE_TEXT)

    def test_ollama_transport_is_injected_and_no_xero_call_is_made(self):
        transport = FakeTransport(valid_ai_response())
        interpreter = OllamaInvoiceInterpreter(transport=transport)
        workflow = PDFInvoiceWorkflow(
            interpreter=interpreter,
            fallback_enabled=True,
        )

        workflow.process_text(INCOMPLETE_TEXT)

        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(transport.calls[0][0].endswith("/api/generate"))
        self.assertNotIn("xero", transport.calls[0][0].casefold())

    def test_module_has_no_xero_or_orchestrator_dependency(self):
        import fieldquo.pdf_invoice_workflow as workflow_module

        self.assertFalse(hasattr(workflow_module, "XeroApiClient"))
        self.assertFalse(hasattr(workflow_module, "invoice_creation_orchestrator"))

    def test_fallback_is_disabled_by_default(self):
        workflow = PDFInvoiceWorkflow()

        self.assertFalse(workflow.fallback_enabled)

    def test_process_pdf_uses_injected_local_extractor_then_parser(self):
        class StubExtractor:
            def extract_text(self, pdf_path):
                self.path = pdf_path
                return COMPLETE_TEXT

        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()
            extractor = StubExtractor()
            result = PDFInvoiceWorkflow(extractor=extractor).process_pdf(pdf_path)

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.source_text, COMPLETE_TEXT)

    def test_parser_error_with_enabled_fallback_still_requires_usable_invoice(self):
        interpreter = FakeInterpreter(
            json.dumps({
                "schema_version": "1",
                "invoice": {
                    "invoice_number": None,
                    "client": None,
                    "issued": None,
                    "due": None,
                    "currency": None,
                    "subtotal": None,
                    "tax": None,
                    "discount": None,
                    "total": None,
                    "line_items": [],
                },
                "evidence": {},
            })
        )

        with self.assertRaises(PDFInvoiceWorkflowError):
            PDFInvoiceWorkflow(
                parser=lambda _text: (_ for _ in ()).throw(ValueError("parse error")),
                interpreter=interpreter,
                fallback_enabled=True,
            ).process_text(INCOMPLETE_TEXT)


if __name__ == "__main__":
    unittest.main()
