import json
import unittest

from fieldquo.invoice_interpreter import (
    InvoiceInterpretationError,
    InvoiceInterpretationRequest,
)
from fieldquo.ollama_invoice_interpreter import (
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_OLLAMA_MODEL,
    OllamaInvoiceInterpreter,
    OllamaTransportError,
    UrllibOllamaTransport,
)


SOURCE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""


def valid_model_response(invoice_number="INV-100"):
    result = {
        "schema_version": "1",
        "invoice": {
            "invoice_number": invoice_number,
            "client": "Example Client",
            "issued": "2025-01-02",
            "due": None,
            "currency": "EUR",
            "subtotal": None,
            "tax": None,
            "discount": None,
            "total": "175.00",
            "line_items": [{
                "description": "Consulting",
                "quantity": "2",
                "unit_price": "75.00",
                "line_total": "150.00",
            }],
        },
        "evidence": {
            "invoice_number": invoice_number,
            "client": "Example Client",
            "issued": "2025-01-02",
            "currency": "EUR",
            "total": "175.00",
            "line_items[0].description": "Consulting",
            "line_items[0].quantity": "2",
            "line_items[0].unit_price": "75.00",
            "line_items[0].line_total": "150.00",
        },
    }
    return json.dumps(result)


class FakeOllamaTransport:
    def __init__(self, generated_response):
        self.generated_response = generated_response
        self.calls = []
        self.error = None

    def post_json(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.error:
            raise self.error
        return {"response": self.generated_response}


class OllamaInvoiceInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeOllamaTransport(valid_model_response())
        self.interpreter = OllamaInvoiceInterpreter(transport=self.transport)
        self.request = InvoiceInterpretationRequest(
            source_text=SOURCE_TEXT,
            deterministic_invoice={"invoice_number": None, "line_items": None},
            missing_fields=("invoice_number", "line_items"),
        )

    def test_request_contains_invoice_text_and_non_invention_instructions(self):
        self.interpreter.interpret(self.request)
        prompt = self.transport.calls[0][1]["prompt"]

        self.assertIn(SOURCE_TEXT, prompt)
        self.assertIn("NÃO invente", prompt)
        self.assertIn("citação literal exata", prompt)

    def test_prompt_requires_evidence_only_for_non_null_invoice_fields(self):
        self.interpreter.interpret(self.request)
        prompt = self.transport.calls[0][1]["prompt"]

        self.assertIn("NÃO inclua em evidence nenhum campo", prompt)
        self.assertIn("não crie uma entrada para ele em evidence", prompt)
        self.assertIn("evidence é um mapa dinâmico", prompt)
        self.assertIn(
            "Cada entrada de evidence deve corresponder exatamente a um campo não nulo",
            prompt,
        )
        self.assertIn("não adicione chaves extras", prompt)

    def test_prompt_specifies_indexed_string_evidence_and_complete_example(self):
        self.interpreter.interpret(self.request)
        prompt = self.transport.calls[0][1]["prompt"]

        for evidence_path in (
            "line_items[0].description",
            "line_items[0].quantity",
            "line_items[0].unit_price",
            "line_items[0].line_total",
        ):
            with self.subTest(evidence_path=evidence_path):
                self.assertIn(evidence_path, prompt)

        self.assertIn("todos os valores dentro de evidence DEVEM ser strings JSON", prompt)
        self.assertIn("Para CADA campo não nulo de invoice", prompt)
        self.assertIn("Para cada campo não nulo de cada line_item", prompt)
        self.assertIn(
            "NUNCA use line_items.description, line_items.quantity, "
            "line_items.unit_price ou line_items.line_total sem índice",
            prompt,
        )
        self.assertIn('"invoice_number": "TEST-1001"', prompt)
        self.assertIn('"discount": null', prompt)
        self.assertIn('"total": "220.00"', prompt)
        self.assertIn('"line_items[0].quantity": "2"', prompt)
        self.assertIn("NUNCA copie estes valores para outra invoice", prompt)

    def test_request_requires_json_and_expected_schema(self):
        self.interpreter.interpret(self.request)
        _, payload, _ = self.transport.calls[0]

        self.assertEqual(payload["format"], "json")
        self.assertFalse(payload["stream"])
        self.assertIn('"schema_version": "1"', payload["prompt"])
        self.assertIn('"line_items"', payload["prompt"])
        self.assertIn("SOMENTE um objeto JSON válido", payload["prompt"])

    def test_default_endpoint_and_model_are_used(self):
        self.interpreter.interpret(self.request)

        url, payload, _ = self.transport.calls[0]
        self.assertEqual(url, f"{DEFAULT_OLLAMA_ENDPOINT}/api/generate")
        self.assertEqual(payload["model"], DEFAULT_OLLAMA_MODEL)

    def test_configured_endpoint_and_model_are_used(self):
        interpreter = OllamaInvoiceInterpreter(
            endpoint="http://localhost:11435/",
            model="custom-model",
            transport=self.transport,
        )
        interpreter.interpret(self.request)

        url, payload, _ = self.transport.calls[0]
        self.assertEqual(url, "http://localhost:11435/api/generate")
        self.assertEqual(payload["model"], "custom-model")

    def test_valid_response_is_accepted(self):
        result = self.interpreter.interpret(self.request)

        self.assertEqual(result, valid_model_response())
        self.assertEqual(len(self.transport.calls), 1)

    def test_invalid_json_is_rejected_by_existing_contract(self):
        self.transport.generated_response = "{bad json"

        with self.assertRaisesRegex(InvoiceInterpretationError, "JSON válido"):
            self.interpreter.interpret(self.request)

    def test_missing_evidence_is_rejected_by_existing_contract(self):
        response = json.loads(valid_model_response())
        del response["evidence"]["client"]
        self.transport.generated_response = json.dumps(response)

        with self.assertRaisesRegex(InvoiceInterpretationError, "evidence"):
            self.interpreter.interpret(self.request)

    def test_evidence_for_null_field_is_rejected_by_existing_contract(self):
        response = json.loads(valid_model_response())
        response["evidence"]["discount"] = None
        self.transport.generated_response = json.dumps(response)

        with self.assertRaisesRegex(InvoiceInterpretationError, "desconhecidos"):
            self.interpreter.interpret(self.request)

    def test_conflicting_deterministic_value_is_rejected(self):
        self.transport.generated_response = valid_model_response("INV-200")
        request = InvoiceInterpretationRequest(
            source_text=SOURCE_TEXT + "Previous invoice: INV-200\n",
            deterministic_invoice={
                "invoice_number": "INV-100",
                "line_items": None,
            },
            missing_fields=(),
        )
        response = json.loads(self.transport.generated_response)
        response["evidence"]["invoice_number"] = "INV-200"
        self.transport.generated_response = json.dumps(response)

        with self.assertRaisesRegex(InvoiceInterpretationError, "conflita"):
            self.interpreter.interpret(request)

    def test_unknown_field_is_rejected_by_existing_contract(self):
        response = json.loads(valid_model_response())
        response["invoice"]["account_code"] = "200"
        self.transport.generated_response = json.dumps(response)

        with self.assertRaisesRegex(InvoiceInterpretationError, "desconhecidos"):
            self.interpreter.interpret(self.request)

    def test_empty_response_is_a_controlled_error(self):
        self.transport.generated_response = " "

        with self.assertRaisesRegex(InvoiceInterpretationError, "vazia"):
            self.interpreter.interpret(self.request)

    def test_transport_error_is_a_controlled_error(self):
        self.transport.error = OllamaTransportError("local transport failure")

        with self.assertRaisesRegex(InvoiceInterpretationError, "transport failure"):
            self.interpreter.interpret(self.request)

    def test_os_error_from_injected_transport_is_a_controlled_error(self):
        self.transport.error = OSError("connection refused")

        with self.assertRaisesRegex(InvoiceInterpretationError, "acessar"):
            self.interpreter.interpret(self.request)

    def test_only_injected_ollama_transport_is_called(self):
        self.interpreter.interpret(self.request)

        self.assertEqual(len(self.transport.calls), 1)
        self.assertTrue(self.transport.calls[0][0].endswith("/api/generate"))
        self.assertNotIn("xero", self.transport.calls[0][0].lower())

    def test_default_transport_is_the_standard_library_implementation(self):
        interpreter = OllamaInvoiceInterpreter()

        self.assertIsInstance(interpreter.transport, UrllibOllamaTransport)


if __name__ == "__main__":
    unittest.main()
