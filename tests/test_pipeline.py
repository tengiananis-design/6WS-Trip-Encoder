import unittest

from services.pipeline import validate_analysis_result


class PipelineValidationTests(unittest.TestCase):
    def test_multiple_driver_groups_keep_each_trip_attached_to_its_driver(self):
        result = {
            "driver": {"name": None, "confidence": "unknown", "evidence": []},
            "driver_groups": [
                {"driver": {"name": "JUAN DELA CRUZ", "confidence": "high"}, "documents": []},
                {"driver": {"name": "PEDRO SANTOS", "confidence": "high"}, "documents": []},
            ],
            "trips": [
                {"driver_name": "JUAN DELA CRUZ", "segments": [
                    {"origin": "SOC 6", "destination": "MFM Pulilan", "movement_type": "outbound"}
                ]},
                {"driver_name": "PEDRO SANTOS", "segments": [
                    {"origin": "SOC 6", "destination": "MFM Marikina", "movement_type": "outbound"}
                ]},
            ],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
            "unresolved_items": [],
        }

        validated = validate_analysis_result(result)

        self.assertEqual(
            [trip["driver_name"] for trip in validated["trips"]],
            ["JUAN DELA CRUZ", "PEDRO SANTOS"],
        )
        self.assertFalse(validated["overall_review_required"])

    def test_extraction_errors_always_require_review(self):
        result = {
            "driver": {"name": "JUAN DELA CRUZ", "confidence": "high"},
            "driver_groups": [
                {"driver": {"name": "JUAN DELA CRUZ", "confidence": "high"}, "documents": []}
            ],
            "trips": [],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
            "unresolved_items": [],
            "extraction_errors": [{"filename": "scan.pdf", "pages": [2]}],
        }

        validated = validate_analysis_result(result)

        self.assertTrue(validated["overall_review_required"])

    def test_unidentified_driver_group_cannot_be_approved(self):
        result = {
            "driver": {"name": None, "confidence": "unknown"},
            "driver_groups": [],
            "trips": [],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
            "unresolved_items": [{"filename": "unknown.pdf"}],
        }

        validated = validate_analysis_result(result)

        self.assertTrue(validated["overall_review_required"])


if __name__ == "__main__":
    unittest.main()
