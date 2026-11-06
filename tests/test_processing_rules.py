
import unittest

from services.driver_grouping import group_documents_by_driver, normalize_driver_name
from services.reimbursement_processor import normalize_amount, normalize_expense, process_reimbursements


class DriverGroupingTests(unittest.TestCase):
    def test_name_normalization_is_case_and_spacing_insensitive(self):
        self.assertEqual(normalize_driver_name("  Juan   Dela Cruz "), "JUAN DELA CRUZ")
        self.assertEqual(normalize_driver_name("juan-dela cruz"), "JUAN DELA CRUZ")

    def test_documents_for_same_driver_are_grouped(self):
        result = group_documents_by_driver([
            {"filename": "trip.pdf", "driver_name": "Juan Dela Cruz", "document_type": "trip_ticket"},
            {"filename": "toll.pdf", "driver": {"name": "JUAN DELA CRUZ"}, "document_type": "toll_reimbursement"},
        ])
        self.assertEqual(result["driver_count"], 1)
        self.assertEqual(result["grouped_documents"][0]["document_count"], 2)
        self.assertTrue(result["grouped_documents"][0]["contains_trip_ticket"])
        self.assertTrue(result["grouped_documents"][0]["contains_toll_reimbursement"])

    def test_different_drivers_are_never_merged(self):
        result = group_documents_by_driver([
            {"filename": "a.pdf", "driver_name": "Driver A"},
            {"filename": "b.pdf", "driver_name": "Driver B"},
        ])
        self.assertEqual(result["driver_count"], 2)

    def test_unknown_driver_goes_to_review_queue(self):
        result = group_documents_by_driver([{"filename": "unknown.pdf"}])
        self.assertEqual(result["driver_count"], 0)
        self.assertEqual(len(result["unidentified_documents"]), 1)
        self.assertTrue(result["review_items"])


class ReimbursementTests(unittest.TestCase):
    def test_amount_normalization(self):
        self.assertEqual(normalize_amount("₱1,250.50"), "1250.50")
        self.assertIsNone(normalize_amount("not an amount"))

    def test_missing_expense_fields_require_review(self):
        record = normalize_expense({"amount": "100"}, "TOLL")
        self.assertEqual(record["status"], "review_required")
        self.assertIsNone(record["date"])
        self.assertIsNone(record["location"])

    def test_toll_and_parking_remain_separate(self):
        result = process_reimbursements({
            "toll_reimbursements": [{"date": "2026-11-01", "location": "Gate", "amount": "100"}],
            "parking_reimbursements": [{"date": "2026-11-01", "location": "Depot", "amount": "50"}],
        })
        self.assertEqual(result["toll_count"], 1)
        self.assertEqual(result["parking_count"], 1)
        self.assertEqual(result["toll_reimbursements"][0]["expense_type"], "TOLL")
        self.assertEqual(result["parking_reimbursements"][0]["expense_type"], "PARKING")


if __name__ == "__main__":
    unittest.main()
