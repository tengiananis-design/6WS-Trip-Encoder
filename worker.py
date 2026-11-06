import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from celery import Celery

from services.storage import S3Storage

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL must be configured for the background worker.")

celery_app = Celery("sixws_trip_encoder", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_time_limit=60 * 60 * 4,
    task_soft_time_limit=60 * 60 * 3 + 50 * 60,
)


@celery_app.task(
    bind=True,
    name="jobs.analyze_batch",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    default_retry_delay=60,
)
def analyze_batch_task(self, batch_id: str):
    storage = S3Storage.from_environment(required=True)
    manifest_key = f"batches/{batch_id}/manifest.json"
    manifest = storage.get_json(manifest_key)
    if not manifest:
        raise ValueError(f"Batch manifest not found: {batch_id}")

    status_key = f"batches/{batch_id}/status.json"
    result_key = f"batches/{batch_id}/result.json"
    if storage.get_json(result_key):
        storage.put_json(status_key, {"batch_id": batch_id, "status": "completed",
                                      "message": "Existing result retained; duplicate task skipped."})
        return {"batch_id": batch_id, "status": "completed"}

    storage.put_json(status_key, {"batch_id": batch_id, "status": "processing",
                                  "task_id": self.request.id})
    try:
        with tempfile.TemporaryDirectory(prefix=f"6ws-{batch_id}-") as temp:
            batch_dir = Path(temp)
            for document in manifest.get("documents", []):
                key = document.get("storage_key")
                filename = Path(document["filename"]).name
                if not key:
                    raise ValueError(f"Missing object storage key for {filename}")
                storage.download_file(key, batch_dir / filename)

            from services.ai_analyzer import analyze_batch_documents
            result = asyncio.run(analyze_batch_documents(batch_dir))
            result["job_status"] = "completed"
            result["completed_at"] = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            storage.put_json(result_key, result)
            storage.put_json(status_key, {
                "batch_id": batch_id, "status": "completed",
                "documents_processed": result.get("documents_processed", 0),
                "documents_total": result.get("documents_total", 0),
                "completed_at": result["completed_at"]
            })
            return {"batch_id": batch_id, "status": "completed"}
    except Exception as error:
        logger.exception("Worker failed to analyze batch %s", batch_id)
        storage.put_json(status_key, {"batch_id": batch_id, "status": "failed",
                                      "error": str(error), "task_id": self.request.id})
        raise
