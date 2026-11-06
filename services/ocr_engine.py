
import logging
import shutil
from pathlib import Path
from typing import Any

import fitz
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# OCR configuration
OCR_LANGUAGE = "eng"
OCR_DPI = 250


def check_ocr_availability() -> bool:
    """Check whether the Tesseract OCR executable is installed."""

    return shutil.which("tesseract") is not None


def extract_page_with_ocr(
    pdf_path: str | Path,
    page_number: int,
    dpi: int = OCR_DPI
) -> dict[str, Any]:
    """
    Convert one scanned PDF page into an image
    and extract its text using Tesseract.
    Page numbering starts at 1.
    """

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if page_number < 1:
        raise ValueError("Page numbers start at 1.")

    try:
        with fitz.open(pdf_path) as document:

            if page_number > len(document):
                raise ValueError("Page number exceeds PDF length.")

            page = document[page_number - 1]

            matrix = fitz.Matrix(
                dpi / 72,
                dpi / 72
            )

            pixmap = page.get_pixmap(
                matrix=matrix,
                colorspace=fitz.csRGB,
                alpha=False
            )

            image = Image.frombytes(
                "RGB",
                [pixmap.width, pixmap.height],
                pixmap.samples
            )

            extracted_text = pytesseract.image_to_string(
                image,
                lang=OCR_LANGUAGE,
                config="--psm 3"
            )

            return {
                "page_number": page_number,
                "text": extracted_text.strip(),
                "character_count": len(extracted_text.strip()),
                "ocr_used": True,
                "status": "completed"
            }

    except Exception as error:
        logger.exception(
            "OCR failed for %s page %s",
            pdf_path.name,
            page_number
        )

        return {
            "page_number": page_number,
            "text": "",
            "character_count": 0,
            "ocr_used": True,
            "status": "failed",
            "error": str(error)
        }


def process_scanned_pages(
    pdf_path: str | Path,
    pages: list[dict],
    dpi: int = OCR_DPI
) -> list[dict]:
    """
    OCR only pages identified by pdf_reader.py
    as requiring OCR.
    """

    results = []

    for page in pages:
        if not page.get("requires_ocr"):
            continue

        page_number = page["page_number"]

        result = extract_page_with_ocr(
            pdf_path,
            page_number,
            dpi
        )

        results.append(result)

    return results


def extract_complete_pdf(
    pdf_path: str | Path,
    dpi: int = OCR_DPI
) -> dict[str, Any]:
    """
    Extract digital text and OCR scanned pages.
    Returns one chronological page collection.
    """

    from services.pdf_reader import extract_pdf_text

    extracted = extract_pdf_text(pdf_path)

    if not check_ocr_availability():
        return {
            **extracted,
            "status": "ocr_not_installed",
            "message": "Tesseract OCR is not installed."
        }

    ocr_results = process_scanned_pages(
        pdf_path,
        extracted["pages"],
        dpi
    )

    ocr_by_page = {
        result["page_number"]: result
        for result in ocr_results
    }

    final_pages = []

    for page in extracted["pages"]:
        page_number = page["page_number"]

        if page.get("requires_ocr"):
            ocr_result = ocr_by_page.get(page_number, {})

            final_pages.append({
                "page_number": page_number,
                "text": ocr_result.get("text", ""),
                "source": "ocr",
                "status": ocr_result.get("status", "failed"),
                "error": ocr_result.get("error")
            })

        else:
            final_pages.append({
                "page_number": page_number,
                "text": page["text"],
                "source": "digital",
                "status": "completed"
            })

    return {
        "filename": extracted["filename"],
        "total_pages": extracted["total_pages"],
        "pages": final_pages,
        "total_characters": sum(
            len(page["text"]) for page in final_pages
        ),
        "status": "completed"
    }
