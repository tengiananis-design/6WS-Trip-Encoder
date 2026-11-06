import unittest

from services.approval import approval_issues, ensure_approvable


class ApprovalValidationTests(unittest.TestCase):
    def test_clean_batch_can_be_approved(self):
        result = {
            "overall_review_required": False,
            "unresolved_items": [],
            "unidentified_documents": [],
            "extraction_errors": [],
            "identification_errors": [],
            "driver_groups": [{
                "driver": {"name": "Driver A"},
                "overall_review_required": False,
                "unresolved_items": [],
                "trips": [{"review_flags": [], "confidence": "high"}],
                "toll_reimbursements": [],
                "parking_reimbursements": [],
            }],
        }
        self.assertEqual(approval_issues(result), [])
        ensure_approvable(result)

    def test_batch_level_review_flag_blocks_approval(self):
        result = {"overall_review_required": True}
        self.assertTrue(any("Batch-level" in issue for issue in approval_issues(result)))
        with self.assertRaises(ValueError):
            ensure_approvable(result)

    def test_unidentified_documents_block_approval(self):
        result = {
            "overall_review_required": False,
            "unidentified_documents": [{"filename": "unknown.pdf"}],
        }
        self.assertTrue(any("unidentified" in issue for issue in approval_issues(result)))

    def test_driver_package_with_flags_blocks_approval(self):
        result = {
            "overall_review_required": False,
            "driver_groups": [{
                "driver": {"name": "Driver A"},
                "overall_review_required": False,
                "unresolved_items": [],
                "trips": [{"review_flags": ["route uncertain"], "confidence": "medium"}],
            }],
        }
        issues = approval_issues(result)
        self.assertTrue(any("review flags" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
