import asyncio
import logging
import os
import tempfile
from pathlib import Path

from celery import Celery

from services.ai_analyzer import analyze_batch_documents
from services.storage import S3Storage

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL is required to start the 6WS background worker.")

celery_app = Celery("6ws_trip_encoder", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(task_track_started=True, task_acks_late=True,
                       worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True)


@celery_app.task(name="sixws.analyze_batch")
def analyze_batch_task(batch_id):
    storage = S3Storage.from_environment(required=True)
    manifest = storage.get_json(f"batches/{batch_id}/manifest.json")
    if not manifest:
        raise ValueError(f"Batch manifest not found: {batch_id}")
    storage.put_json(f"batches/{batch_id}/status.json",
                     {"batch_id": batch_id, "status": "processing"})
    try:
        with tempfile.TemporaryDirectory(prefix=f"6ws-{batch_id}-") as temp_dir:
            batch_dir = Path(temp_dir)
            for item in manifest.get("documents", []):
                filename, key = item.get("filename"), item.get("storage_key")
                if not filename or not key:
                    raise ValueError("Manifest contains a missing source object reference.")
                storage.get_file(key, batch_dir / filename)
            result = asyncio.run(analyze_batch_documents(batch_dir))
        result["job_status"] = "completed"
        storage.put_json(f"batches/{batch_id}/result.json", result)
        storage.put_json(f"batches/{batch_id}/status.json",
                         {"batch_id": batch_id, "status": "completed",
                          "documents_processed": result.get("documents_processed", 0),
                          "documents_total": result.get("documents_total", 0)})
        return {"batch_id": batch_id, "status": "completed"}
    except Exception as error:
        logger.exception("Batch analysis failed: %s", batch_id)
        storage.put_json(f"batches/{batch_id}/status.json",
                         {"batch_id": batch_id, "status": "failed", "error": str(error)})
        # Do not automatically restart the entire batch: that would repeat already-billed AI calls.
        # Individual page-window requests have their own bounded retry policy.
        raise
