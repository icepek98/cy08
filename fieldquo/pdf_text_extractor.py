"""Small injection boundary for local PDF text extraction."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class PDFTextExtractionError(RuntimeError):
    """Raised when an injected PDF text extractor cannot read a document."""


class InvalidPDFFileError(PDFTextExtractionError):
    """Raised when a selected file is not a PDF document."""


class EmptyPDFFileError(PDFTextExtractionError):
    """Raised when a PDF file has no bytes."""


class PDFWithoutTextError(PDFTextExtractionError):
    """Raised when a PDF contains no extractable text."""


class PDFReadError(PDFTextExtractionError):
    """Raised when the local PDF backend cannot read a document."""


class PDFExtractorUnavailableError(PDFTextExtractionError):
    """Raised when the Poppler ``pdftotext`` executable is unavailable."""


class PDFTextExtractor(Protocol):
    """Interface implemented by an optional local PDF extraction backend."""

    def extract_text(self, pdf_path: str | Path) -> str:
        """Return locally extracted text from a PDF."""
        ...


class PopplerPDFTextExtractor:
    """Extract text locally with Poppler's ``pdftotext`` executable."""

    def __init__(self, *, timeout: float = 60) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    def extract_text(self, pdf_path: str | Path) -> str:
        path = Path(pdf_path).expanduser()
        if path.suffix.lower() != ".pdf":
            raise InvalidPDFFileError("O arquivo selecionado não tem extensão .pdf.")
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo PDF não encontrado: {path}")
        try:
            if path.stat().st_size == 0:
                raise EmptyPDFFileError("O arquivo PDF está vazio.")
            with path.open("rb") as pdf_file:
                header = pdf_file.read(1024)
        except OSError as exc:
            raise PDFReadError("Não foi possível ler o arquivo PDF.") from exc
        if b"%PDF-" not in header:
            raise InvalidPDFFileError("O arquivo selecionado não é um PDF válido.")

        executable = shutil.which("pdftotext")
        if executable is None:
            raise PDFExtractorUnavailableError(
                "Poppler (pdftotext) não está instalado ou não está no PATH."
            )
        try:
            result = subprocess.run(
                [executable, "-layout", "-enc", "UTF-8", str(path.resolve()), "-"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise PDFReadError("A leitura do PDF excedeu o tempo limite.") from exc
        except OSError as exc:
            raise PDFReadError("Não foi possível executar a leitura do PDF.") from exc
        if result.returncode != 0:
            raise PDFReadError(
                "Poppler não conseguiu ler o arquivo PDF selecionado."
            )
        if not result.stdout.strip():
            raise PDFWithoutTextError(
                "O PDF não contém texto extraível; OCR não está habilitado."
            )
        return result.stdout


def extract_pdf_text(
    pdf_path: str | Path,
    extractor: PDFTextExtractor,
) -> str:
    """Validate an injected extractor's result without selecting a PDF library."""
    path = Path(pdf_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo PDF não encontrado: {path}")
    if not callable(getattr(extractor, "extract_text", None)):
        raise TypeError("extractor must implement extract_text(pdf_path)")

    text = extractor.extract_text(path)
    if not isinstance(text, str):
        raise PDFTextExtractionError("O extrator de PDF não retornou texto.")
    if not text.strip():
        raise PDFTextExtractionError(
            "O PDF não contém texto extraível; nenhuma inferência foi realizada."
        )
    return text
