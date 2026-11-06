import unittest

from services.ai_analyzer import assemble_batch_result


class ManualDriverGroupingTests(unittest.TestCase):
    def make_document(self, filename, driver_name, trip_destination):
        driver = {"name": driver_name, "id": None, "confidence": "unknown", "evidence": []}
        return {
            "driver": driver,
            "documents": [{
                "filename": filename,
                "document_type": "trip_ticket",
                "driver": driver,
            }],
            "source_filename": filename,
            "trips": [{
                "trip_date": "2026-11-01",
                "origin": "SOC 6",
                "destination": trip_destination,
                "segments": [{
                    "origin": "SOC 6",
                    "destination": trip_destination,
                    "source_pages": [1],
                }],
                "source_filename": filename,
            }],
            "toll_reimbursements": [],
            "parking_reimbursements": [],
            "passway_reimbursements": [],
            "unresolved_items": [],
        }

    def test_unidentified_document_is_separated_for_manual_assignment(self):
        result = assemble_batch_result(
            "batch-test",
            [self.make_document("ticket-a.pdf", None, "Bolinao")],
            [],
            1,
        )
        self.assertEqual(result["driver_count"], 0)
        self.assertEqual(
            result["grouping"]["unidentified_documents"][0]["filename"],
            "ticket-a.pdf",
        )

    def test_manual_driver_name_regroups_document_and_trip(self):
        document = self.make_document("ticket-a.pdf", None, "Bolinao")
        document["driver"] = {"name": "Amado Canar", "confidence": "manual"}
        document["documents"][0]["driver"] = document["driver"]
        result = assemble_batch_result("batch-test", [document], [], 1)
        self.assertEqual(result["driver_count"], 1)
        self.assertEqual(result["drivers"][0]["driver"]["name"], "Amado Canar")
        self.assertEqual(result["trips"][0]["driver_name"], "Amado Canar")
        self.assertEqual(result["grouping"]["unidentified_documents"], [])

    def test_similar_but_different_names_are_not_merged(self):
        result = assemble_batch_result(
            "batch-test",
            [
                self.make_document("ticket-a.pdf", "Juan Dela Cruz", "Bolinao"),
                self.make_document("ticket-b.pdf", "Juan Dela Cruz Jr", "Dasol"),
            ],
            [],
            2,
        )
        self.assertEqual(result["driver_count"], 2)


if __name__ == "__main__":
    unittest.main()
