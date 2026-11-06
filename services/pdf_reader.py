
import logging
from pathlib import Path
from typing import Any

import fitz
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str | Path) -> dict[str, Any]:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")

    pages = []
    try:
        with fitz.open(pdf_path) as document:
            for page_number, page in enumerate(document, start=1):
                text = page.get_text("text", sort=True).strip()
                # Some scans contain a tiny footer or OCR fragment only; OCR sparse pages.
                pages.append({
                    "page_number": page_number,
                    "text": text,
                    "has_text": bool(text),
                    "requires_ocr": len(text) < 25
                })
            total_pages = len(document)
    except Exception as error:
        logger.exception("PyMuPDF extraction failed for %s", pdf_path.name)
        raise RuntimeError(f"Unable to read PDF: {error}") from error

    return {
        "filename": pdf_path.name,
        "total_pages": total_pages,
        "total_characters": sum(len(page["text"]) for page in pages),
        "pages": pages,
        "status": "extracted"
    }


def extract_pdf_metadata(pdf_path: str | Path) -> dict:
    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata
    return {
        "filename": Path(pdf_path).name,
        "page_count": len(reader.pages),
        "title": metadata.title if metadata else None,
        "author": metadata.author if metadata else None,
        "subject": metadata.subject if metadata else None,
        "creator": metadata.creator if metadata else None
    }


def extract_multiple_pdfs(pdf_paths: list[str | Path]) -> dict:
    documents, errors = [], []
    for pdf_path in pdf_paths:
        try:
            documents.append(extract_pdf_text(pdf_path))
        except Exception as error:
            errors.append({"filename": Path(pdf_path).name, "error": str(error)})
    return {
        "document_count": len(documents),
        "documents": documents,
        "errors": errors,
        "status": "completed" if not errors else "completed_with_errors"
    }
