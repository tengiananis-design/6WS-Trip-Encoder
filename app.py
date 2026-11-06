import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

APP_NAME = "6WS Trip Encoder"
APP_VERSION = "1.1.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(__import__("os").getenv("DATA_DIR", str(BASE_DIR / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
MAX_UPLOAD_FILES = int(__import__("os").getenv("MAX_UPLOAD_FILES", "150"))
MAX_FILE_BYTES = int(__import__("os").getenv("MAX_UPLOAD_SIZE_MB", "100")) * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

for directory in (UPLOAD_DIR, RESULT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(APP_NAME)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def safe_batch_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value or ""):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    return value


@app.get("/health")
async def health():
    return {"application": APP_NAME, "version": APP_VERSION, "status": "online",
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
async def application_status():
    import os
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEETS_ID")
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service-account.json")
    google_credentials_available = bool(service_account_json) or os.path.isfile(service_account_file)
    shared_storage_available = bool(
        os.getenv("S3_BUCKET")
        and os.getenv("S3_ACCESS_KEY_ID")
        and os.getenv("S3_SECRET_ACCESS_KEY")
    )
    return {
        "status": "online",
        "pdf_processing": "available",
        "ai_analysis": "configured" if os.getenv("GEMINI_API_KEY") else "missing_api_key",
        "google_sheets": "configured" if (spreadsheet_id and google_credentials_available) else "missing_spreadsheet_or_credentials",
        "storage": "shared_object_storage" if shared_storage_available else "local_filesystem",
        "distributed_queue": "configured" if (os.getenv("REDIS_URL") and shared_storage_available) else "local_fallback"
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request, "app_name": APP_NAME, "version": APP_VERSION
    })


@app.post("/api/upload")
async def upload_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files selected.")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"Select no more than {MAX_UPLOAD_FILES} PDFs per batch.")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    uploaded_files = []

    try:
        for index, uploaded in enumerate(files, start=1):
            original_name = Path(uploaded.filename or f"document_{index}.pdf").name
            if not original_name.lower().endswith(".pdf"):
                continue

            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._") or f"document_{index}"
            stored_name = f"{index:04d}_{safe_stem}.pdf"
            destination = batch_dir / stored_name
            total = 0
            header = b""

            try:
                with destination.open("wb") as output:
                    while True:
                        chunk = await uploaded.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        if total == 0:
                            header = chunk[:5]
                        total += len(chunk)
                        if total > MAX_FILE_BYTES:
                            raise HTTPException(status_code=413, detail=f"{original_name} exceeds the configured {MAX_FILE_BYTES // (1024 * 1024)} MB per-file limit.")
                        output.write(chunk)

                if not header.startswith(b"%PDF"):
                    destination.unlink(missing_ok=True)
                    continue

                uploaded_files.append({
                    "filename": stored_name,
                    "original_filename": original_name,
                    "size_bytes": total
                })
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            finally:
                await uploaded.close()

        if not uploaded_files:
            raise HTTPException(status_code=400, detail="No valid PDF files were uploaded.")

        batch_info = {
            "batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(uploaded_files),
            "documents": uploaded_files,
            "status": "uploaded"
        }
        from services import storage
        if storage.shared_storage_enabled():
            for item in uploaded_files:
                storage.upload_file(f"uploads/{batch_id}/{item['filename']}", batch_dir / item["filename"], "application/pdf")
            storage.save_json(f"uploads/{batch_id}/batch.json", batch_info)
        (batch_dir / "batch.json").write_text(json.dumps(batch_info, indent=2), encoding="utf-8")
        return {"message": "Documents uploaded successfully.", **batch_info}
    except Exception:
        for item in batch_dir.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
        batch_dir.rmdir()
        raise


@app.get("/api/batches")
async def list_batches():
    from services import storage
    batches = []
    if storage.shared_storage_enabled():
        for key in storage.list_keys("uploads"):
            if key.endswith("/batch.json"):
                try:
                    batches.append(storage.load_json(key))
                except Exception:
                    logger.warning("Could not read shared batch metadata at %s", key)
        return {"batches": sorted(batches, key=lambda item: item.get("created_at", ""), reverse=True)}
    if not UPLOAD_DIR.exists():
        return {"batches": batches}
    for batch_dir in sorted(UPLOAD_DIR.iterdir(), reverse=True):
        batch_file = batch_dir / "batch.json"
        if batch_dir.is_dir() and batch_file.exists():
            try:
                batches.append(json.loads(batch_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                logger.warning("Could not read batch metadata at %s", batch_file)
    return {"batches": batches}


def _run_analysis_job(batch_id: str, batch_dir: Path):
    import asyncio
    from services.job_store import write_job
    from services.ai_analyzer import analyze_batch_documents
    from services.pipeline import validate_analysis_result

    try:
        write_job(batch_dir, batch_id=batch_id, status="processing", stage="starting",
                  completed_documents=0, total_documents=len(list(batch_dir.glob("*.pdf"))),
                  error=None, started_at=datetime.now(timezone.utc).isoformat())
        def progress(done, total, filename):
            write_job(batch_dir, status="processing", stage="analyzing_documents",
                      completed_documents=done, total_documents=total,
                      current_filename=filename)
        result = asyncio.run(analyze_batch_documents(batch_dir, progress_callback=progress))
        result = validate_analysis_result(result)
        result["batch_id"] = batch_id
        result["approval_status"] = "pending_review"
        result_file = RESULT_DIR / f"{batch_id}.json"
        temp_file = result_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(result_file)
        write_job(batch_dir, status="completed", stage="review_ready",
                  completed_documents=result.get("documents_processed", 0),
                  result_available=True, finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        logger.exception("Analysis failed for batch %s", batch_id)
        write_job(batch_dir, status="failed", stage="error", error=str(error),
                  finished_at=datetime.now(timezone.utc).isoformat())


@app.post("/api/analyze/{batch_id}")
async def analyze_batch(batch_id: str, background_tasks: BackgroundTasks):
    from services.job_store import read_job, write_job
    batch_id = safe_batch_id(batch_id)
    from services import storage
    batch_dir = UPLOAD_DIR / batch_id
    if not batch_dir.is_dir():
        if storage.shared_storage_enabled() and storage.exists(f"uploads/{batch_id}/batch.json"):
            batch_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(status_code=404, detail="Batch not found.")
    current = read_job(batch_dir)
    if current and current.get("status") == "processing":
        return {"message": "Analysis is already running.", "batch_id": batch_id, "job": current}
    if (RESULT_DIR / f"{batch_id}.json").is_file() or (storage.shared_storage_enabled() and storage.exists(f"results/{batch_id}.json")):
        raise HTTPException(status_code=409, detail="Analysis result already exists. Review it instead of running analysis again.")
    if storage.shared_storage_enabled() and storage.exists(f"jobs/{batch_id}.json"):
        existing_job = storage.load_json(f"jobs/{batch_id}.json")
        if existing_job.get("status") in ("queued", "processing"):
            return {"message": "Analysis is already queued or running.", "batch_id": batch_id, "job": existing_job}
    if storage.shared_storage_enabled() and storage.exists(f"uploads/{batch_id}/batch.json"):
        total = int(storage.load_json(f"uploads/{batch_id}/batch.json").get("document_count", 0))
    else:
        total = len(list(batch_dir.glob("*.pdf")))
    if storage.shared_storage_enabled() and __import__("os").getenv("REDIS_URL"):
        from services.tasks import analyze_batch_task
        task = analyze_batch_task.apply_async(args=[batch_id])
        job = {"batch_id": batch_id, "task_id": task.id, "status": "queued", "stage": "queued",
               "completed_documents": 0, "total_documents": total,
               "queued_at": datetime.now(timezone.utc).isoformat()}
        storage.save_json(f"jobs/{batch_id}.json", job)
        return {"message": "Distributed analysis queued.", "batch_id": batch_id, "job": job}
    write_job(batch_dir, batch_id=batch_id, status="queued", stage="queued",
              completed_documents=0, total_documents=total, error=None,
              queued_at=datetime.now(timezone.utc).isoformat())
    background_tasks.add_task(_run_analysis_job, batch_id, batch_dir)
    return {"message": "Local fallback queued. Configure shared storage and REDIS_URL for worker processing.", "batch_id": batch_id,
            "job": read_job(batch_dir)}


@app.get("/api/jobs/{batch_id}")
async def get_analysis_job(batch_id: str):
    from services.job_store import read_job
    batch_id = safe_batch_id(batch_id)
    from services import storage
    batch_dir = UPLOAD_DIR / batch_id
    if not batch_dir.is_dir() and not (storage.shared_storage_enabled() and storage.exists(f"jobs/{batch_id}.json")):
        raise HTTPException(status_code=404, detail="Batch not found.")
    if storage.shared_storage_enabled() and storage.exists(f"jobs/{batch_id}.json"):
        job = storage.load_json(f"jobs/{batch_id}.json")
        if job.get("task_id"):
            from services.celery_app import celery_app
            task = celery_app.AsyncResult(job["task_id"])
            meta = task.info if isinstance(task.info, dict) else {}
            job.update(meta)
            if task.successful():
                job.update({"status": "completed", **(task.result or {})})
            elif task.failed():
                job.update({"status": "failed", "error": str(task.result)})
            else:
                job["status"] = "processing" if task.state in ("STARTED", "PROGRESS", "RETRY") else "queued"
                job["celery_state"] = task.state
        return job
    job = read_job(batch_dir)
    if not job:
        return {"batch_id": batch_id, "status": "not_started"}
    return job


@app.get("/api/results/{batch_id}")
async def get_analysis_result(batch_id: str):
    from services import storage
    batch_id = safe_batch_id(batch_id)
    result_file = RESULT_DIR / f"{batch_id}.json"
    try:
        if storage.shared_storage_enabled() and storage.exists(f"results/{batch_id}.json"):
            return storage.load_json(f"results/{batch_id}.json")
        if not result_file.is_file():
            raise HTTPException(status_code=404, detail="Analysis result not found.")
        return json.loads(result_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Stored analysis result is invalid JSON.")


@app.put("/api/review/{batch_id}")
async def save_reviewed_result(batch_id: str, request: Request):
    from services import storage
    batch_id = safe_batch_id(batch_id)
    result_key = f"results/{batch_id}.json"
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not (result_file.is_file() or (storage.shared_storage_enabled() and storage.exists(result_key))):
        raise HTTPException(status_code=404, detail="Analysis result not found.")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="A reviewed JSON result is required.")
    reviewed = payload.get("result")
    if not isinstance(reviewed, dict):
        raise HTTPException(status_code=400, detail="The result field must be a JSON object.")
    for field in ("driver_groups", "documents", "trips", "toll_reimbursements", "parking_reimbursements", "unresolved_items"):
        if field in reviewed and not isinstance(reviewed[field], list):
            raise HTTPException(status_code=400, detail=f"{field} must be a list.")
    if not isinstance(reviewed.get("driver"), dict):
        raise HTTPException(status_code=400, detail="driver must be an object.")
    reviewed["batch_id"] = batch_id
    reviewed["approval_status"] = "pending_review"
    reviewed["review_saved_at"] = datetime.now(timezone.utc).isoformat()
    # Nested record flags must also be resolved; clearing only the summary
    # list must never make a flagged trip or receipt exportable.
    nested_flags = []
    for trip in reviewed.get("trips", []):
        nested_flags.extend(trip.get("review_flags", []))
    for collection in ("toll_reimbursements", "parking_reimbursements"):
        for expense in reviewed.get(collection, []):
            nested_flags.extend(expense.get("review_flags", []))
    reviewed["overall_review_required"] = bool(
        reviewed.get("unresolved_items")
        or nested_flags
        or reviewed.get("extraction_errors")
        or not (reviewed.get("driver_groups") or reviewed.get("driver", {}).get("name"))
        or (
            not reviewed.get("driver_groups")
            and reviewed.get("driver", {}).get("confidence") not in ("high", "verified")
        )
        or any(
            group.get("driver", {}).get("confidence") not in ("high", "verified")
            for group in reviewed.get("driver_groups", [])
        )
    )
    if storage.shared_storage_enabled():
        storage.save_json(result_key, reviewed)
    else:
        temp_file = result_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(reviewed, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(result_file)
    return {"message": "Reviewed result saved. It remains unapproved until separately approved.", "batch_id": batch_id, "overall_review_required": reviewed["overall_review_required"]}



@app.post("/api/approve/{batch_id}")
async def approve_batch(batch_id: str, request: Request):
    from services import storage
    batch_id = safe_batch_id(batch_id)
    result_key = f"results/{batch_id}.json"
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not (result_file.is_file() or (storage.shared_storage_enabled() and storage.exists(result_key))):
        raise HTTPException(status_code=404, detail="No analysis result available.")

    try:
        confirmation = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Approval confirmation is required.")

    if confirmation.get("review_confirmed") is not True:
        raise HTTPException(status_code=400, detail="Explicit human review confirmation is required.")

    result = storage.load_json(result_key) if storage.shared_storage_enabled() else json.loads(result_file.read_text(encoding="utf-8"))
    if result.get("overall_review_required") is not False:
        raise HTTPException(status_code=409, detail="This batch still has unresolved review flags.")
    if result.get("approval_status") in ("exported", "exporting", "export_failed_manual_reconciliation"):
        raise HTTPException(status_code=409, detail="This batch is already exporting, exported, or needs manual reconciliation. Automatic retry is blocked to avoid duplicate spreadsheet rows.")

    result["approval_status"] = "exporting"
    result["approved_at"] = datetime.now(timezone.utc).isoformat()
    result["export_started_at"] = datetime.now(timezone.utc).isoformat()
    if storage.shared_storage_enabled():
        storage.save_json(result_key, result)
    else:
        result_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        from services.google_sheets import export_approved_batch
        export_result = await export_approved_batch(batch_id=batch_id, result=result)
        result["approval_status"] = "exported"
        result["export_result"] = export_result
        if storage.shared_storage_enabled():
            storage.save_json(result_key, result)
        else:
            result_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"message": "Approved batch exported.", "batch_id": batch_id, "export": export_result}
    except Exception as error:
        logger.exception("Export failed for approved batch %s", batch_id)
        result["approval_status"] = "export_failed_manual_reconciliation"
        result["export_error"] = str(error)
        if storage.shared_storage_enabled():
            storage.save_json(result_key, result)
        else:
            result_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        raise HTTPException(status_code=502, detail=f"Approval saved, but export failed: {error}")


@app.get("/api")
async def api_information():
    return {
        "name": APP_NAME, "version": APP_VERSION,
        "workflow": ["Upload PDFs", "Extract text and OCR", "Analyze documents",
                     "Validate trip and reimbursement records", "Human review",
                     "Approve and export"]
    }
