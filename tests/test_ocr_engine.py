import unittest
from unittest.mock import patch

from services.ocr_engine import extract_complete_pdf


class HybridOCRTests(unittest.TestCase):
    def test_sparse_digital_text_is_preserved_alongside_ocr(self):
        extraction = {
            "filename": "hybrid.pdf",
            "total_pages": 1,
            "pages": [{
                "page_number": 1,
                "text": "Page 1",
                "requires_ocr": True,
            }],
        }
        ocr = [{
            "page_number": 1,
            "text": "TRIP TICKET DRIVER JUAN DELA CRUZ",
            "status": "completed",
        }]

        with patch("services.pdf_reader.extract_pdf_text", return_value=extraction), patch(
            "services.ocr_engine.check_ocr_availability", return_value=True
        ), patch("services.ocr_engine.process_scanned_pages", return_value=ocr):
            result = extract_complete_pdf("hybrid.pdf")

        page = result["pages"][0]
        self.assertIn("Page 1", page["text"])
        self.assertIn("TRIP TICKET DRIVER JUAN DELA CRUZ", page["text"])
        self.assertEqual(page["source"], "digital+ocr")
        self.assertEqual(page["status"], "completed")

    def test_failed_ocr_does_not_discard_existing_digital_text(self):
        extraction = {
            "filename": "hybrid.pdf",
            "total_pages": 1,
            "pages": [{
                "page_number": 1,
                "text": "Trip Ticket",
                "requires_ocr": True,
            }],
        }
        ocr = [{
            "page_number": 1,
            "text": "",
            "status": "failed",
            "error": "tesseract error",
        }]

        with patch("services.pdf_reader.extract_pdf_text", return_value=extraction), patch(
            "services.ocr_engine.check_ocr_availability", return_value=True
        ), patch("services.ocr_engine.process_scanned_pages", return_value=ocr):
            result = extract_complete_pdf("hybrid.pdf")

        page = result["pages"][0]
        self.assertEqual(page["text"], "Trip Ticket")
        self.assertEqual(page["status"], "completed")
        self.assertEqual(page["error"], "tesseract error")


if __name__ == "__main__":
    unittest.main()
