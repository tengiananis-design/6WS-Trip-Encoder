import unittest
from unittest.mock import AsyncMock, patch

from services.ai_analyzer import (
    _page_windows,
    _merge_chunk_records,
    merge_page_window_analyses,
    analyze_documents_with_retry,
)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_ai_error_is_retried(self):
        with patch("services.ai_analyzer.analyze_documents",
                   side_effect=[RuntimeError("temporary"), {"driver": {"name": "Driver"}}]) as analyze, \
             patch("services.ai_analyzer.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await analyze_documents_with_retry([{"filename": "ticket.pdf"}])
        self.assertEqual(result["driver"]["name"], "Driver")
        self.assertEqual(analyze.call_count, 2)
        sleep.assert_awaited_once_with(2)


class PageWindowTests(unittest.TestCase):
    def test_windows_cover_all_pages_with_overlap(self):
        pages = [{"page_number": n} for n in range(1, 27)]
        windows = _page_windows(pages, size=12, overlap=2)
        flattened = [page["page_number"] for window in windows for page in window]
        self.assertEqual(windows[0][0]["page_number"], 1)
        self.assertEqual(windows[-1][-1]["page_number"], 26)
        self.assertEqual(flattened.count(11), 2)
        self.assertEqual(flattened.count(12), 2)
        self.assertEqual(flattened.count(13), 1)

    def test_short_document_uses_one_window(self):
        pages = [{"page_number": 1}, {"page_number": 2}]
        self.assertEqual(_page_windows(pages), [pages])

    def test_invalid_overlap_is_rejected(self):
        with self.assertRaises(ValueError):
            _page_windows([{"page_number": 1}], size=4, overlap=4)

    def test_duplicate_receipt_on_overlap_merges_page_evidence(self):
        rows = [
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [12], "_analysis_window": 0},
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [12], "_analysis_window": 1},
        ]
        merged = _merge_chunk_records(rows, "toll_reimbursements")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_pages"], [12])

    def test_two_identical_receipts_in_same_window_are_preserved(self):
        rows = [
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [12], "_analysis_window": 0},
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [12], "_analysis_window": 0},
        ]
        self.assertEqual(len(_merge_chunk_records(rows, "toll_reimbursements")), 2)

    def test_different_receipts_are_not_collapsed(self):
        rows = [
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [12], "_analysis_window": 0},
            {"date": "2026-11-01", "amount": "100", "tag_number": "A1",
             "source_pages": [13], "_analysis_window": 1},
        ]
        self.assertEqual(len(_merge_chunk_records(rows, "toll_reimbursements")), 2)

    def test_conflicting_driver_ids_across_windows_require_review(self):
        merged = merge_page_window_analyses([
            {"driver": {"name": "Sample Driver", "id": "100"},
             "documents": [{"filename": "ticket.pdf", "document_type": "trip_ticket", "pages_used": [1]}]},
            {"driver": {"name": "Sample Driver", "id": "200"},
             "documents": [{"filename": "ticket.pdf", "document_type": "trip_ticket", "pages_used": [11]}]}
        ], "ticket.pdf", 20)
        self.assertTrue(merged["chunked_analysis_review_required"])
        self.assertEqual(merged["unresolved_items"][0]["driver_id_candidates"], ["100", "200"])

    def test_chunked_document_is_flagged_for_human_review(self):
        merged = merge_page_window_analyses([
            {"driver": {"name": "Sample Driver"},
             "documents": [{"filename": "ticket.pdf", "document_type": "trip_ticket", "pages_used": [1]}],
             "trips": [{"line_haul_trip_number": "LH-1", "trip_date": "2026-11-01",
                        "source_pages": [1], "segments": []}]},
            {"driver": {"name": "Sample Driver"},
             "documents": [{"filename": "ticket.pdf", "document_type": "trip_ticket", "pages_used": [11]}],
             "trips": [{"line_haul_trip_number": "LH-1", "trip_date": "2026-11-01",
                        "source_pages": [11], "segments": []}]}
        ], "ticket.pdf", 20)
        self.assertTrue(merged["chunked_analysis_review_required"])
        self.assertEqual(len(merged["trips"]), 1)
        self.assertIn("Long PDF used multiple AI page windows", merged["trips"][0]["review_flags"][0])


if __name__ == "__main__":
    unittest.main()
