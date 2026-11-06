import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class ManualAssignmentEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.upload_dir = root / "uploads"
        self.result_dir = root / "results"
        self.upload_dir.mkdir()
        self.result_dir.mkdir()
        self.batch_id = "test-batch"
        self.batch_dir = self.upload_dir / self.batch_id
        self.batch_dir.mkdir()
        self.manifest = {
            "batch_id": self.batch_id,
            "documents": [
                {"filename": "0001_trip.pdf"},
                {"filename": "0002_toll.pdf"},
            ],
        }
        (self.batch_dir / "batch.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.upload_patch = patch.object(app_module, "UPLOAD_DIR", self.upload_dir)
        self.result_patch = patch.object(app_module, "RESULT_DIR", self.result_dir)
        self.upload_patch.start()
        self.result_patch.start()
        self.env_patch = patch.dict(os.environ, {
            "S3_BUCKET": "",
            "S3_ACCESS_KEY_ID": "",
            "S3_SECRET_ACCESS_KEY": "",
        })
        self.env_patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.upload_patch.stop()
        self.result_patch.stop()
        self.env_patch.stop()
        self.temporary.cleanup()

    def test_saves_manual_driver_assignments_to_batch_manifest(self):
        response = self.client.put(
            f"/api/assignments/{self.batch_id}",
            json={"assignments": {"0002_toll.pdf": "Juan Dela Cruz"}},
        )
        self.assertEqual(response.status_code, 200)
        saved = json.loads((self.batch_dir / "batch.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["manual_assignments"], {"0002_toll.pdf": "JUAN DELA CRUZ"})

    def test_rejects_unknown_uploaded_filename(self):
        response = self.client.put(
            f"/api/assignments/{self.batch_id}",
            json={"assignments": {"not-uploaded.pdf": "Juan Dela Cruz"}},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_assignment_changes_after_analysis_starts(self):
        (self.batch_dir / "job.json").write_text(
            json.dumps({"status": "processing"}), encoding="utf-8"
        )
        response = self.client.put(
            f"/api/assignments/{self.batch_id}",
            json={"assignments": {"0002_toll.pdf": "Juan Dela Cruz"}},
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
