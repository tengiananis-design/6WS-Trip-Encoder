import unittest
from unittest.mock import MagicMock, patch

from services import google_sheets


class SheetsExportIdempotencyTests(unittest.TestCase):
    def test_repeated_batch_export_is_skipped_after_ledger_match(self):
        service = MagicMock()
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"sheetId": 1, "title": google_sheets.TRIP_SHEET_NAME}},
                {"properties": {"sheetId": 2, "title": google_sheets.TOLL_SHEET_NAME}},
                {"properties": {"sheetId": 3, "title": google_sheets.PARKING_SHEET_NAME}},
                {"properties": {"sheetId": 4, "title": google_sheets.EXPORT_LEDGER_SHEET}},
            ]
        }
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [["Batch ID"]]
        }
        service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {}

        result = {
            "approval_status": "approved",
            "driver": {"name": "Driver A"},
            "trips": [],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
        }

        with (
            patch.object(google_sheets, "get_sheets_service", return_value=service),
            patch.object(google_sheets, "_existing_export", side_effect=[False, True]),
        ):
            first = google_sheets._export_approved_batch_sync("batch-12345678", result)
            second = google_sheets._export_approved_batch_sync("batch-12345678", result)

        self.assertEqual(first["status"], "exported")
        self.assertEqual(second["status"], "already_exported")
        self.assertEqual(service.spreadsheets.return_value.batchUpdate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
