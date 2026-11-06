import asyncio
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.celery_app import celery_app
from services import storage

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="sixws.analyze_batch", max_retries=2, default_retry_delay=30)
def analyze_batch_task(self, batch_id: str):
    if not storage.shared_storage_enabled():
        raise RuntimeError("Worker requires shared object storage. Configure S3-compatible storage before queueing jobs.")
    manifest_key = f"uploads/{batch_id}/batch.json"
    manifest = storage.load_json(manifest_key)
    total = int(manifest.get("document_count", 0))
    self.update_state(state="PROGRESS", meta={
        "batch_id": batch_id, "stage": "downloading", "completed_documents": 0,
        "total_documents": total, "current_filename": None
    })

    try:
        with tempfile.TemporaryDirectory(prefix=f"6ws-{batch_id}-") as temporary:
            batch_dir = Path(temporary)
            storage.download_batch(batch_id, batch_dir)

            from services.ai_analyzer import analyze_batch_documents
            from services.pipeline import validate_analysis_result

            def progress(done, count, filename):
                self.update_state(state="PROGRESS", meta={
                    "batch_id": batch_id, "stage": "analyzing_documents",
                    "completed_documents": done, "total_documents": count,
                    "current_filename": filename
                })

            result = asyncio.run(analyze_batch_documents(batch_dir, progress_callback=progress))
            result = validate_analysis_result(result)
            result["batch_id"] = batch_id
            result["approval_status"] = "pending_review"
            storage.save_json(f"results/{batch_id}.json", result)
            return {
                "batch_id": batch_id, "stage": "review_ready",
                "completed_documents": result.get("documents_processed", total),
                "total_documents": total, "result_available": True,
                "finished_at": datetime.now(timezone.utc).isoformat()
            }
    except Exception as error:
        logger.exception("6WS batch analysis failed: %s", batch_id)
        raise
