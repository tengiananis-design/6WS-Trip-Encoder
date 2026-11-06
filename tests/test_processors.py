import unittest

from services.trip_processor import determine_movement_type, process_trip
from services.reimbursement_processor import normalize_expense


class TripProcessorTests(unittest.TestCase):
    def test_soc_to_same_soc_parking_is_parking(self):
        self.assertEqual(determine_movement_type("SOC 6", "SOC 6 PARKING"), "parking")

    def test_soc_to_mfm_is_outbound_not_backload(self):
        self.assertEqual(determine_movement_type("SOC 6", "MFM Pulilan"), "outbound")

    def test_non_soc_to_mfm_is_backload(self):
        self.assertEqual(determine_movement_type("Dasol Hub", "MFM Pulilan"), "backload")

    def test_non_soc_to_non_mfm_is_not_backload(self):
        self.assertEqual(determine_movement_type("Dasol Hub", "Bolinao Hub"), "intermediate")

    def test_missing_route_is_flagged(self):
        trip = process_trip({"origin": "SOC 6", "destination": None, "segments": []})
        self.assertEqual(trip["status"], "review_required")
        self.assertTrue(trip["review_flags"])

    def test_time_order_is_used_when_available(self):
        trip = process_trip({
            "origin": "SOC 6",
            "destination": "Bolinao",
            "segments": [
                {"origin": "Dasol", "destination": "MFM Pulilan", "departure_time": "10:00 AM"},
                {"origin": "SOC 6", "destination": "Dasol", "departure_time": "06:00 AM"},
            ]
        })
        self.assertEqual(trip["segments"][0]["origin"], "SOC 6")
        self.assertEqual(trip["segments"][1]["movement_type"], "backload")


class ReimbursementTests(unittest.TestCase):
    def test_toll_record_missing_entry_exit_is_flagged(self):
        row = normalize_expense({
            "date": "2026-11-01",
            "driver_name": "Sample Driver",
            "amount": "150.00",
            "rfid_toll_charges": "Autosweep",
            "entry_point": None,
            "exit_point": None,
        }, "TOLL")
        self.assertIn("Toll entry or exit point is missing.", row["review_flags"])
        self.assertEqual(row["amount"], "150.00")

    def test_invalid_amount_is_not_silently_zeroed(self):
        row = normalize_expense({
            "trip_date": "2026-11-01",
            "driver_name": "Sample Driver",
            "amount": "unknown",
            "vendor_name": "Parking vendor",
            "address": "Sample address",
        }, "PARKING")
        self.assertIsNone(row["amount"])
        self.assertIn("Missing or invalid reimbursement amount.", row["review_flags"])


if __name__ == "__main__":
    unittest.main()
