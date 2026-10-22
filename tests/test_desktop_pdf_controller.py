import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from desktop_pdf_controller import (
    DesktopPDFController,
    DesktopPDFError,
    classify_selected_file,
)
from fieldquo.pdf_invoice_workflow import (
    PDFInvoiceWorkflowError,
    PDFInvoiceWorkflowResult,
)


class DesktopPDFControllerTests(unittest.TestCase):
    def setUp(self):
        self.validator = Mock()
        self.validator.validate_license.return_value = (True, "Licença válida")
        self.workflow = Mock()
        self.result = PDFInvoiceWorkflowResult(
            invoice={
                "invoice_number": "INV-1",
                "client": "Cliente",
                "issued": None,
                "currency": "EUR",
                "total": 10,
                "line_items": [],
            },
            source_text="Invoice",
            fallback_used=False,
            missing_fields=(),
            parser_result=None,
        )
        self.workflow.process_pdf.return_value = self.result
        self.controller = DesktopPDFController(
            license_validator=self.validator,
            workflow=self.workflow,
        )

    def test_file_classification_accepts_pdf_and_preserves_legacy_formats(self):
        self.assertEqual(classify_selected_file("invoice.PDF"), "pdf")
        self.assertEqual(classify_selected_file("records.csv"), "csv")
        self.assertEqual(classify_selected_file("records.xlsx"), "xlsx")
        self.assertIsNone(classify_selected_file("records.txt"))

    def test_selected_pdf_is_validated_and_forwarded_to_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            result = self.controller.process_pdf(pdf_path, " license-key ")

        self.assertIs(result, self.result)
        self.validator.validate_license.assert_called_once_with("license-key")
        self.workflow.process_pdf.assert_called_once_with(pdf_path)

    def test_non_pdf_or_missing_pdf_is_rejected_before_license_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            other_path = Path(directory) / "invoice.csv"
            other_path.touch()
            with self.assertRaisesRegex(DesktopPDFError, "PDF existente"):
                self.controller.process_pdf(other_path, "license-key")

            with self.assertRaisesRegex(DesktopPDFError, "PDF existente"):
                self.controller.process_pdf(
                    Path(directory) / "missing.pdf", "license-key"
                )

        self.validator.validate_license.assert_not_called()
        self.workflow.process_pdf.assert_not_called()

    def test_invalid_license_blocks_workflow(self):
        self.validator.validate_license.return_value = (False, "Licença expirada")
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            with self.assertRaisesRegex(DesktopPDFError, "Licença expirada"):
                self.controller.process_pdf(pdf_path, "license-key")

        self.workflow.process_pdf.assert_not_called()

    def test_workflow_error_is_reported_as_desktop_error(self):
        self.workflow.process_pdf.side_effect = PDFInvoiceWorkflowError(
            "backend indisponível"
        )
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            with self.assertRaisesRegex(
                DesktopPDFError, "Não foi possível processar o PDF: backend indisponível"
            ):
                self.controller.process_pdf(pdf_path, "license-key")

    def test_empty_license_blocks_before_external_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "invoice.pdf"
            pdf_path.touch()

            with self.assertRaisesRegex(DesktopPDFError, "chave de licença"):
                self.controller.process_pdf(pdf_path, " ")

        self.validator.validate_license.assert_not_called()
        self.workflow.process_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
