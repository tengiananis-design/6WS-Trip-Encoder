import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import ai_analyzer


class HundredPdfPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_hundred_pdfs_group_into_ten_independent_driver_packages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch_dir = Path(temp_dir)
            records = []
            for index in range(100):
                stored = f"file-{index:03d}.pdf"
                (batch_dir / stored).write_bytes(b"mock-pdf")
                records.append({
                    "filename": "same-original-name.pdf",
                    "stored_filename": stored,
                })
            (batch_dir / "batch.json").write_text(
                json.dumps({"documents": records}), encoding="utf-8"
            )

            def fake_extract(path, original_name):
                return {
                    "filename": original_name,
                    "stored_filename": path.name,
                    "total_pages": 1,
                    "extraction_status": "completed",
                    "pages": [{
                        "page_number": 1,
                        "text": f"evidence {path.name}",
                        "status": "completed",
                    }],
                }

            def fake_identify(documents):
                return {
                    doc["stored_filename"]: {
                        "document_id": doc["stored_filename"],
                        "driver_name": f"Driver {int(doc['stored_filename'][5:8]) % 10}",
                        "document_type": "trip_ticket",
                        "confidence": "high",
                    }
                    for doc in documents
                }

            def fake_analyze(driver_name, documents):
                return {
                    "driver": {"name": driver_name},
                    "trips": [],
                    "toll_reimbursements": [],
                    "parking_reimbursements": [],
                    "unresolved_items": [],
                    "overall_review_required": False,
                }

            with (
                patch.object(ai_analyzer, "_extract_one", side_effect=fake_extract),
                patch.object(ai_analyzer, "identify_document_owners", side_effect=fake_identify),
                patch.object(ai_analyzer, "analyze_driver", side_effect=fake_analyze),
            ):
                result = await ai_analyzer.analyze_batch_documents(batch_dir)

        self.assertEqual(result["documents_received"], 100)
        self.assertEqual(result["documents_processed"], 100)
        self.assertEqual(result["driver_count"], 10)
        self.assertEqual(sum(group["document_count"] for group in result["driver_groups"]), 100)
        self.assertEqual(len(result["unidentified_documents"]), 0)
        self.assertFalse(result["overall_review_required"])


if __name__ == "__main__":
    unittest.main()
