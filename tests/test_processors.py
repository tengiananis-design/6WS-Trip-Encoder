import unittest

from services.driver_grouping import group_documents_by_driver, normalize_driver_name
from services.trip_processor import contains_mfm_token, determine_movement_type, process_trip
from services.reimbursement_processor import normalize_amount, normalize_expense


class DriverGroupingTests(unittest.TestCase):
    def test_names_are_normalized_without_merging_different_names(self):
        self.assertEqual(normalize_driver_name("  Juan  Dela Cruz "), "JUAN DELA CRUZ")
        grouped = group_documents_by_driver([
            {"filename": "a.pdf", "driver_name": "Juan Dela Cruz"},
            {"filename": "b.pdf", "driver_name": "Pedro Santos"},
            {"filename": "c.pdf"}
        ])
        self.assertEqual(grouped["driver_count"], 2)
        self.assertEqual(len(grouped["unidentified_documents"]), 1)

    def test_manual_assignment_conflict_is_held_for_review(self):
        grouped = group_documents_by_driver(
            [{"filename": "a.pdf", "driver_name": "Juan Dela Cruz"}],
            {"a.pdf": "Pedro Santos"}
        )
        self.assertEqual(grouped["driver_count"], 0)
        self.assertEqual(len(grouped["review_items"]), 1)


class TripRuleTests(unittest.TestCase):
    def test_mfm_requires_standalone_token(self):
        self.assertTrue(contains_mfm_token("MFM Pulilan"))
        self.assertFalse(contains_mfm_token("AMFM Hub"))
        self.assertFalse(contains_mfm_token("MFM2"))

    def test_soc_to_mfm_is_outbound(self):
        self.assertEqual(determine_movement_type("SOC 6", "MFM Pulilan"), "outbound")

    def test_model_cannot_override_soc_to_mfm_outbound_rule(self):
        trip = process_trip({"segments": [{
            "origin": "SOC 6",
            "destination": "MFM Pulilan",
            "movement_type": "backload",
        }]})
        self.assertEqual(trip["segments"][0]["movement_type"], "outbound")

    def test_model_cannot_mark_non_mfm_leg_as_backload(self):
        trip = process_trip({"segments": [{
            "origin": "Dasol Hub",
            "destination": "Bolinao Hub",
            "movement_type": "backload",
        }]})
        self.assertEqual(trip["segments"][0]["movement_type"], "intermediate")

    def test_same_soc_parking_leg_is_parking(self):
        self.assertEqual(determine_movement_type("SOC 6", "SOC 6 PARKING"), "parking")


    def test_non_soc_to_mfm_is_backload(self):
        self.assertEqual(determine_movement_type("Dasol Hub", "MFM Pulilan"), "backload")

    def test_non_soc_to_non_mfm_is_not_backload(self):
        self.assertEqual(determine_movement_type("Dasol Hub", "Bolinao Hub"), "intermediate")

    def test_soc_destination_is_return(self):
        self.assertEqual(determine_movement_type("Bolinao Hub", "SOC 6"), "return")

    def test_missing_segment_fields_are_flagged(self):
        trip = process_trip({"segments": [{"origin": "SOC 6", "destination": None}]})
        self.assertEqual(trip["status"], "review_required")
        self.assertTrue(trip["review_flags"])

    def test_segments_are_sorted_by_actual_departure_time(self):
        trip = process_trip({"segments": [
            {"origin": "MFM Pulilan", "destination": "SOC 6", "departure_time": "10:00", "movement_type": "outbound"},
            {"origin": "SOC 6", "destination": "MFM Pulilan", "departure_time": "06:00", "movement_type": "return"},
        ]})
        self.assertEqual(trip["segments"][0]["departure_time"], "06:00")
        self.assertEqual(trip["segments"][1]["departure_time"], "10:00")

    def test_source_filename_and_page_references_are_preserved(self):
        reference = {"filename": "trip-ticket.pdf", "page_number": 3, "fact": "SOC 6 departure"}
        trip = process_trip({
            "source_references": [reference],
            "segments": [{
                "origin": "SOC 6",
                "destination": "MFM Pulilan",
                "source_references": [reference],
            }],
        })
        self.assertEqual(trip["source_references"], [reference])
        self.assertEqual(trip["segments"][0]["source_references"], [reference])

    def test_missing_trip_date_and_line_haul_number_require_review(self):
        trip = process_trip({
            "segments": [{
                "origin": "SOC 6",
                "destination": "MFM Pulilan",
                "source_references": [{"filename": "trip.pdf", "page_number": 1}],
            }],
            "source_references": [{"filename": "trip.pdf", "page_number": 1}],
        })
        self.assertTrue(any("Trip date is missing" in flag for flag in trip["review_flags"]))
        self.assertTrue(any("Line Haul Trip No." in flag for flag in trip["review_flags"]))


class ReimbursementTests(unittest.TestCase):
    def test_amount_normalization(self):
        self.assertEqual(normalize_amount("₱1,250.50"), "1250.50")
        self.assertIsNone(normalize_amount("not an amount"))

    def test_incomplete_expense_requires_review(self):
        expense = normalize_expense({"date": None, "location": "NLEX", "amount": None}, "TOLL")
        self.assertEqual(expense["status"], "review_required")

    def test_reimbursement_source_references_are_preserved(self):
        reference = {"filename": "toll.pdf", "page_number": 2, "fact": "Toll amount"}
        expense = normalize_expense({
            "date": "2026-11-06",
            "location": "NLEX",
            "amount": "120",
            "source_references": [reference],
        }, "TOLL")
        self.assertEqual(expense["source_references"], [reference])


if __name__ == "__main__":
    unittest.main()
