import unittest
from unittest.mock import patch

from services import ai_analyzer


class DocumentIdentificationBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_document_list_is_split_into_bounded_parallel_chunks(self):
        documents = [
            {
                "filename": f"same-name-{index % 2}.pdf",
                "stored_filename": f"unique-{index}.pdf",
                "total_pages": 1,
                "pages": [{"page_number": 1, "text": f"Driver {index}"}],
            }
            for index in range(23)
        ]
        call_sizes = []

        def fake_generate_json(prompt):
            import json
            payload = prompt.split("Documents:" + chr(10), 1)[1]
            items = json.loads(payload)
            call_sizes.append(len(items))
            return {
                "documents": [{
                    "document_id": item["document_id"],
                    "driver_name": f"Driver {item['document_id']}",
                    "document_type": "trip_ticket",
                    "confidence": "high",
                } for item in items]
            }

        with patch.object(ai_analyzer, "generate_json", side_effect=fake_generate_json):
            identified, errors = await ai_analyzer.identify_documents_in_parallel(documents)

        self.assertEqual(errors, [])
        self.assertEqual(len(identified), 23)
        self.assertEqual(sorted(call_sizes), [3, 10, 10])
        self.assertEqual(len(set(identified)), 23)


if __name__ == "__main__":
    unittest.main()
