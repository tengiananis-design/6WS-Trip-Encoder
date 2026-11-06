
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

import fitz
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "eng")
OCR_DPI = int(os.getenv("OCR_DPI", "200"))
OCR_MAX_WORKERS = max(1, min(int(os.getenv("OCR_MAX_WORKERS", "2")), 4))


@lru_cache(maxsize=1)
def check_ocr_availability() -> bool:
    return shutil.which("tesseract") is not None


def extract_page_with_ocr(pdf_path: str | Path, page_number: int, dpi: int = OCR_DPI) -> dict[str, Any]:
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
            scale = dpi / 72
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            extracted_text = pytesseract.image_to_string(image, lang=OCR_LANGUAGE, config="--psm 3")
            text = extracted_text.strip()
            return {
                "page_number": page_number,
                "text": text,
                "character_count": len(text),
                "ocr_used": True,
                "status": "completed" if text else "empty"
            }
    except Exception as error:
        logger.exception("OCR failed for %s page %s", pdf_path.name, page_number)
        return {"page_number": page_number, "text": "", "character_count": 0, "ocr_used": True, "status": "failed", "error": str(error)}


def process_scanned_pages(pdf_path: str | Path, pages: list[dict], dpi: int = OCR_DPI) -> list[dict]:
    page_numbers = [page["page_number"] for page in pages if page.get("requires_ocr")]
    if not page_numbers:
        return []
    # A small bounded pool overlaps page rendering and OCR without overwhelming Render's free instance.
    with ThreadPoolExecutor(max_workers=min(OCR_MAX_WORKERS, len(page_numbers))) as pool:
        futures = [pool.submit(extract_page_with_ocr, pdf_path, number, dpi) for number in page_numbers]
        return [future.result() for future in futures]


def extract_complete_pdf(pdf_path: str | Path, dpi: int = OCR_DPI) -> dict[str, Any]:
    from services.pdf_reader import extract_pdf_text
    extracted = extract_pdf_text(pdf_path)
    if not check_ocr_availability():
        return {**extracted, "status": "ocr_not_installed", "message": "Tesseract OCR is not installed."}

    ocr_results = process_scanned_pages(pdf_path, extracted["pages"], dpi)
    ocr_by_page = {result["page_number"]: result for result in ocr_results}
    final_pages = []
    for page in extracted["pages"]:
        number = page["page_number"]
        if page.get("requires_ocr"):
            item = ocr_by_page.get(number, {})
            final_pages.append({
                "page_number": number,
                "text": item.get("text", ""),
                "source": "ocr",
                "status": item.get("status", "failed"),
                "error": item.get("error")
            })
        else:
            final_pages.append({"page_number": number, "text": page["text"], "source": "digital", "status": "completed"})
    return {
        "filename": extracted["filename"],
        "total_pages": extracted["total_pages"],
        "pages": final_pages,
        "total_characters": sum(len(page["text"]) for page in final_pages),
        "status": "completed"
    }
