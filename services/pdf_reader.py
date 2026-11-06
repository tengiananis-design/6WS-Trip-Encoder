
import logging
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str | Path) -> dict[str, Any]:
    """
    Extract text from every page of a PDF.
    Preserves page numbers for later trip analysis.
    """

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")

    pages = []

    try:
        document = fitz.open(pdf_path)

        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text", sort=True).strip()
            # Sparse text layers are common in scanned PDFs that contain only
            # a page number or a faint OCR layer. Send those pages through OCR.
            requires_ocr = len(text) < 30

            pages.append({
                "page_number": page_number,
                "text": text,
                "has_text": bool(text),
                "requires_ocr": requires_ocr
            })

        total_pages = len(document)
        document.close()

    except Exception as error:
        logger.exception("PyMuPDF extraction failed")
        raise RuntimeError(
            f"Unable to read PDF: {error}"
        ) from error

    total_characters = sum(
        len(page["text"]) for page in pages
    )

    return {
        "filename": pdf_path.name,
        "total_pages": total_pages,
        "total_characters": total_characters,
        "pages": pages,
        "status": "extracted"
    }


def extract_pdf_metadata(pdf_path: str | Path) -> dict:
    """Read basic PDF metadata."""

    pdf_path = Path(pdf_path)

    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata

    return {
        "filename": pdf_path.name,
        "page_count": len(reader.pages),
        "title": metadata.title if metadata else None,
        "author": metadata.author if metadata else None,
        "subject": metadata.subject if metadata else None,
        "creator": metadata.creator if metadata else None
    }


def extract_multiple_pdfs(
    pdf_paths: list[str | Path]
) -> dict:
    """
    Extract multiple PDFs while keeping each document separate.
    This allows several trip, toll, and parking PDFs
    to be processed as one driver's submission.
    """

    documents = []
    errors = []

    for pdf_path in pdf_paths:
        try:
            document = extract_pdf_text(pdf_path)
            documents.append(document)

        except Exception as error:
            errors.append({
                "filename": Path(pdf_path).name,
                "error": str(error)
            })

    return {
        "document_count": len(documents),
        "documents": documents,
        "errors": errors,
        "status": (
            "completed" if not errors
            else "completed_with_errors"
        )
    }
