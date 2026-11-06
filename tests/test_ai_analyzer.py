import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.ai_analyzer import analyze_batch_documents


class DriverScopedAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_related_pdfs_are_reanalyzed_together_within_driver_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = Path(temporary)
            (batch / "trip-1.pdf").write_bytes(b"%PDF-1.4")
            (batch / "trip-2.pdf").write_bytes(b"%PDF-1.4")

            def fake_extract(path):
                return {
                    "pages": [{"page_number": 1, "text": "Trip ticket evidence", "status": "success"}],
                    "total_pages": 1,
                    "status": "success",
                }

            def fake_analyze(documents):
                if len(documents) == 1:
                    return {
                        "driver": {
                            "name": "JUAN DELA CRUZ",
                            "confidence": "high",
                            "evidence": [{"filename": documents[0]["filename"]}],
                        },
                        "trips": [],
                        "toll_reimbursements": [],
                        "parking_reimbursements": [],
                        "unresolved_items": [],
                    }
                return {
                    "driver": {"name": "JUAN DELA CRUZ", "confidence": "high", "evidence": []},
                    "trips": [
                        {
                            "trip_ticket_number": "TT-001",
                            "trip_date": "2026-11-01",
                            "segments": [
                                {"sequence": 1, "origin": "SOC 6", "destination": "MFM Pulilan"}
                            ],
                        }
                    ],
                    "toll_reimbursements": [],
                    "parking_reimbursements": [],
                    "unresolved_items": [],
                }

            with patch("services.ai_analyzer.extract_complete_pdf", side_effect=fake_extract), patch(
                "services.ai_analyzer.analyze_documents", side_effect=fake_analyze
            ) as analyze:
                result = await analyze_batch_documents(batch)

            self.assertEqual(analyze.call_count, 3)  # two identity checks + one driver-level reconciliation
            self.assertEqual(len(result["driver_groups"]), 1)
            self.assertEqual(result["driver_groups"][0]["driver"]["name"], "JUAN DELA CRUZ")
            self.assertEqual(len(result["trips"]), 1)
            self.assertEqual(result["trips"][0]["driver_name"], "JUAN DELA CRUZ")
            self.assertEqual(result["analysis_requests"], 3)


if __name__ == "__main__":
    unittest.main()
