import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from main import LicenseApp
from fieldquo.pdf_invoice_preview import (
    PDFInvoicePreviewError,
    PDFInvoicePreviewProcessor,
    format_pdf_invoice_preview,
)
from fieldquo.pdf_text_extractor import (
    EmptyPDFFileError,
    InvalidPDFFileError,
    PDFReadError,
    PDFWithoutTextError,
    PopplerPDFTextExtractor,
)
from xero.pdf_invoice_service import PDFInvoiceService


COMPLETE_TEXT = """\
Invoice number: INV-100
Client: Example Client
Issued: 2025-01-02
Currency: EUR
Total: 175.00
Description | Quantity | Unit Price | Line Total
Consulting | 2 | 75.00 | 150.00
"""
PARTIAL_TEXT = """\
Client: Example Client
Currency: EUR
Total: 20.00
"""
DESCRIPTION_WITHOUT_QUANTITY = """\
Client: Example Client
Currency: EUR
Total: 20.00
Description | Quantity | Unit Price | Line Total
Consulting |  | 20.00 | 20.00
"""
QUANTITY_WITHOUT_DESCRIPTION = """\
Client: Example Client
Currency: EUR
Total: 20.00
Description | Quantity | Unit Price | Line Total
 | 2 | 10.00 | 20.00
"""


def write_minimal_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(pdf)


class PopplerPDFTextExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pdf_path = Path(self.temp_dir.name) / "invoice.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4\nmock document")

    @patch("fieldquo.pdf_text_extractor.subprocess.run")
    @patch("fieldquo.pdf_text_extractor.shutil.which", return_value="/usr/bin/pdftotext")
    def test_extracts_text_from_valid_pdf(self, _which, run):
        run.return_value = Mock(returncode=0, stdout=COMPLETE_TEXT, stderr="")

        text = PopplerPDFTextExtractor().extract_text(self.pdf_path)

        self.assertEqual(text, COMPLETE_TEXT)
        self.assertEqual(run.call_args.args[0][-2:], [str(self.pdf_path.resolve()), "-"])

    def test_extracts_text_from_real_text_pdf(self):
        import shutil

        if shutil.which("pdftotext") is None:
            self.skipTest("Poppler pdftotext is not installed.")
        write_minimal_text_pdf(self.pdf_path, "Local PDF extraction check")

        text = PopplerPDFTextExtractor().extract_text(self.pdf_path)

        self.assertIn("Local PDF extraction check", text)

    def test_rejects_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            PopplerPDFTextExtractor().extract_text(self.pdf_path.with_name("missing.pdf"))

    def test_rejects_wrong_extension(self):
        other_path = self.pdf_path.with_suffix(".txt")
        other_path.write_bytes(b"%PDF-1.4\nmock document")
        with self.assertRaises(InvalidPDFFileError):
            PopplerPDFTextExtractor().extract_text(other_path)

    def test_rejects_empty_pdf(self):
        self.pdf_path.write_bytes(b"")
        with self.assertRaises(EmptyPDFFileError):
            PopplerPDFTextExtractor().extract_text(self.pdf_path)

    def test_rejects_pdf_without_text(self):
        with patch(
            "fieldquo.pdf_text_extractor.subprocess.run",
            return_value=Mock(returncode=0, stdout=" \n", stderr=""),
        ), patch(
            "fieldquo.pdf_text_extractor.shutil.which",
            return_value="/usr/bin/pdftotext",
        ):
            with self.assertRaises(PDFWithoutTextError):
                PopplerPDFTextExtractor().extract_text(self.pdf_path)

    def test_reports_backend_read_error_without_logging_pdf_text(self):
        with patch(
            "fieldquo.pdf_text_extractor.subprocess.run",
            return_value=Mock(returncode=1, stdout="", stderr="sensitive pdf text"),
        ), patch(
            "fieldquo.pdf_text_extractor.shutil.which",
            return_value="/usr/bin/pdftotext",
        ):
            with self.assertRaises(PDFReadError) as caught:
                PopplerPDFTextExtractor().extract_text(self.pdf_path)
        self.assertNotIn("sensitive pdf text", str(caught.exception))


class PDFInvoicePreviewProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pdf_path = Path(self.temp_dir.name) / "invoice.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4\nmock document")
        self.extractor = Mock()
        self.extractor.extract_text.return_value = COMPLETE_TEXT
        self.processor = PDFInvoicePreviewProcessor(extractor=self.extractor)

    def test_preview_preserves_recognized_description_quantity_and_amounts(self):
        preview = self.processor.process_pdf(self.pdf_path)

        self.assertEqual(preview.invoice["client"], "Example Client")
        self.assertEqual(preview.invoice["issued"], date(2025, 1, 2))
        self.assertEqual(preview.invoice["total"], Decimal("175.00"))
        self.assertEqual(preview.invoice["line_items"][0]["description"], "Consulting")
        self.assertEqual(preview.invoice["line_items"][0]["quantity"], Decimal("2"))
        self.assertTrue(preview.complete)
        self.assertEqual(preview.source_identity, "fieldquo_pdf")
        self.assertIsNone(preview.invoice["due"])

    def test_preview_keeps_absent_description_and_quantity_absent(self):
        self.extractor.extract_text.return_value = PARTIAL_TEXT

        preview = self.processor.process_pdf(self.pdf_path)

        self.assertFalse(preview.complete)
        self.assertIsNone(preview.invoice["line_items"])
        self.assertIn("line_items", preview.missing_fields)

    def test_preview_preserves_description_when_quantity_is_missing(self):
        self.extractor.extract_text.return_value = DESCRIPTION_WITHOUT_QUANTITY

        preview = self.processor.process_pdf(self.pdf_path)

        line = preview.invoice["line_items"][0]
        self.assertEqual(line["description"], "Consulting")
        self.assertIsNone(line["quantity"])
        self.assertIn("line_items[0].quantity", preview.missing_fields)

    def test_preview_preserves_quantity_when_description_is_missing(self):
        self.extractor.extract_text.return_value = QUANTITY_WITHOUT_DESCRIPTION

        preview = self.processor.process_pdf(self.pdf_path)

        line = preview.invoice["line_items"][0]
        self.assertIsNone(line["description"])
        self.assertEqual(line["quantity"], Decimal("2"))
        self.assertIn("line_items[0].description", preview.missing_fields)

    def test_preview_reports_multiple_missing_fields(self):
        self.extractor.extract_text.return_value = "Total: 20.00\n"

        preview = self.processor.process_pdf(self.pdf_path)

        self.assertIn("invoice_number", preview.missing_fields)
        self.assertIn("client", preview.missing_fields)
        self.assertIn("line_items", preview.missing_fields)

    def test_rejects_missing_and_wrong_extension_files(self):
        with self.assertRaisesRegex(PDFInvoicePreviewError, "não existe"):
            self.processor.process_pdf(self.pdf_path.with_name("missing.pdf"))
        with self.assertRaisesRegex(PDFInvoicePreviewError, "extensão"):
            self.processor.process_pdf(self.pdf_path.with_suffix(".csv"))
        self.extractor.extract_text.assert_not_called()

    def test_extraction_errors_are_presented_without_source_text(self):
        self.extractor.extract_text.side_effect = PDFReadError("Falha de leitura.")

        with self.assertRaisesRegex(PDFInvoicePreviewError, "Falha de leitura"):
            self.processor.process_pdf(self.pdf_path)

    def test_preview_formatter_shows_success_data_and_blocked_emission(self):
        preview = self.processor.process_pdf(self.pdf_path)

        lines = format_pdf_invoice_preview(preview)
        rendered = "\n".join(lines)

        self.assertIn(
            "Pré-visualização — nenhuma invoice foi enviada ao Xero.", rendered
        )
        self.assertIn("Pronto para próxima etapa", rendered)
        self.assertIn("descrição=Consulting", rendered)
        self.assertIn("quantidade=2", rendered)
        self.assertIn("ContactID", rendered)
        self.assertIn("OAuth Xero", rendered)

    @patch("requests.sessions.Session.request")
    @patch.object(PDFInvoiceService, "create_pdf_invoice")
    @patch("xero.invoice_creation_orchestrator.create_pdf_invoice_in_xero")
    @patch("xero.invoice_creation_orchestrator.create_fieldquo_invoice_in_xero")
    def test_preview_does_not_call_emission_or_make_http_requests(
        self, create_fieldquo_invoice, create_pdf_invoice_in_xero,
        create_pdf_invoice_service, request
    ):
        create_fieldquo_invoice.reset_mock()
        create_pdf_invoice_in_xero.reset_mock()
        create_pdf_invoice_service.reset_mock()

        preview = self.processor.process_pdf(self.pdf_path)

        self.assertIsNotNone(preview)
        create_pdf_invoice_service.assert_not_called()
        create_pdf_invoice_in_xero.assert_not_called()
        create_fieldquo_invoice.assert_not_called()
        request.assert_not_called()

    @patch("main.AutomationExecutor")
    def test_desktop_pdf_path_shows_preview_without_license_or_automation(
        self, automation_executor
    ):
        app = LicenseApp.__new__(LicenseApp)
        app.pdf_preview_processor = self.processor
        messages = []
        app.log = messages.append
        app.after = lambda _delay, callback, *args: callback(*args)
        app._restore_button = Mock()

        app._process_license_and_automation(
            selected_file=str(self.pdf_path),
            selected_file_kind="pdf",
            cancel_event=threading.Event(),
            demo_mode=False,
        )

        rendered = "\n".join(messages)
        self.assertIn(
            "Pré-visualização — nenhuma invoice foi enviada ao Xero.", rendered
        )
        automation_executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
