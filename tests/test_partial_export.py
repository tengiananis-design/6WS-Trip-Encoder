import unittest
from unittest.mock import patch

from services.google_sheets import PartialExportError, export_approved_batch


class PartialGoogleSheetsExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_failure_records_completed_sheet_and_failed_sheet(self):
        result = {
            "approval_status": "exporting",
            "driver": {"name": "MULTIPLE"},
            "trips": [],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
        }

        def append(service, sheet_name, rows):
            if sheet_name == "Toll Reimbursements":
                raise RuntimeError("simulated API failure")
            return {"sheet": sheet_name, "rows_added": 1}

        with patch("services.google_sheets.get_sheets_service", return_value=object()), patch(
            "services.google_sheets.append_rows", side_effect=append
        ):
            with self.assertRaises(PartialExportError) as caught:
                await export_approved_batch("batch-1", result)

        self.assertEqual(caught.exception.failed_sheet, "Toll Reimbursements")
        self.assertEqual(caught.exception.completed_exports, [
            {"sheet": "Trip Encoding", "rows_added": 1}
        ])


if __name__ == "__main__":
    unittest.main()
