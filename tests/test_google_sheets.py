import unittest

from services.google_sheets import prepare_reimbursement_rows, prepare_trip_rows


class GoogleSheetsExportPreparationTests(unittest.TestCase):
    def test_multi_driver_trip_rows_keep_driver_identity(self):
        result = {
            "driver": {"name": None},
            "trips": [
                {
                    "driver_name": "JUAN DELA CRUZ",
                    "trip_date": "2026-11-01",
                    "trip_ticket_number": "TT-001",
                    "line_haul_trip_number": "LH-001",
                    "segments": [
                        {
                            "sequence": 1,
                            "origin": "SOC 6",
                            "destination": "MFM Pulilan",
                            "departure_time": "06:00",
                            "arrival_time": "08:00",
                            "movement_type": "outbound",
                        }
                    ],
                },
                {
                    "driver_name": "PEDRO SANTOS",
                    "trip_date": "2026-11-01",
                    "trip_ticket_number": "TT-002",
                    "line_haul_trip_number": "LH-002",
                    "segments": [
                        {
                            "sequence": 1,
                            "origin": "SOC 6",
                            "destination": "MFM Marikina",
                            "departure_time": "07:00",
                            "arrival_time": "09:00",
                            "movement_type": "outbound",
                        }
                    ],
                },
            ],
        }

        rows = prepare_trip_rows(result)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "JUAN DELA CRUZ")
        self.assertEqual(rows[1][0], "PEDRO SANTOS")
        self.assertEqual(rows[0][6], "MFM Pulilan")
        self.assertEqual(rows[1][6], "MFM Marikina")

    def test_reimbursement_rows_keep_their_own_driver_names(self):
        records = [
            {
                "driver_name": "JUAN DELA CRUZ",
                "date": "2026-11-01",
                "location": "NLEX",
                "amount": "250.00",
                "receipt_reference": "TOLL-001",
                "source_pages": [1, 2],
            },
            {
                "driver_name": "PEDRO SANTOS",
                "date": "2026-11-01",
                "location": "SLEX",
                "amount": "180.00",
                "receipt_reference": "TOLL-002",
                "source_pages": [3],
            },
        ]

        rows = prepare_reimbursement_rows(records, "MULTIPLE")

        self.assertEqual(rows[0][0], "JUAN DELA CRUZ")
        self.assertEqual(rows[1][0], "PEDRO SANTOS")
        self.assertEqual(rows[0][3], "250.00")
        self.assertEqual(rows[1][3], "180.00")


if __name__ == "__main__":
    unittest.main()
