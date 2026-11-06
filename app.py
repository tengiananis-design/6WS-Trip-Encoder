import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
import base64
import hmac
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

APP_NAME = "6WS Trip Encoder"
APP_VERSION = "1.3.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
for folder in (DATA_DIR, UPLOAD_DIR, RESULT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

MAX_FILES_PER_BATCH = int(os.getenv("MAX_FILES_PER_BATCH", "200"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_BATCH_SIZE_MB = int(os.getenv("MAX_BATCH_SIZE_MB", "1024"))
CHUNK_SIZE = 1024 * 1024
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(APP_NAME)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(DashboardAuthMiddleware)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def local_manifest(batch_id):
    return UPLOAD_DIR / batch_id / "batch.json"


def local_status(batch_id):
    return RESULT_DIR / f"{batch_id}.status.json"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def durable_storage():
    # Use remote object storage only when the Redis worker queue is also enabled.
    # Otherwise files must remain local so the development fallback can read them.
    if not (os.getenv("REDIS_URL") and os.getenv("S3_BUCKET")):
        return None
    from services.storage import S3Storage
    return S3Storage.from_environment(required=True)


def durable_queue_enabled():
    return bool(os.getenv("REDIS_URL") and os.getenv("S3_BUCKET"))


def set_batch_status(batch_id, status, **extra):
    state = {"batch_id": batch_id, "status": status, "updated_at": utc_now(), **extra}
    storage = durable_storage()
    if storage:
        storage.put_json(f"batches/{batch_id}/status.json", state)
    else:
        atomic_json(local_status(batch_id), state)
    manifest = get_manifest(batch_id)
    if manifest:
        manifest["status"] = status
        manifest["updated_at"] = utc_now()
        save_manifest(batch_id, manifest)
    return state


def save_manifest(batch_id, manifest):
    storage = durable_storage()
    if storage:
        storage.put_json(f"batches/{batch_id}/manifest.json", manifest)
    else:
        atomic_json(local_manifest(batch_id), manifest)


def get_manifest(batch_id):
    storage = durable_storage()
    if storage:
        return storage.get_json(f"batches/{batch_id}/manifest.json")
    return read_json(local_manifest(batch_id))


def get_status(batch_id):
    storage = durable_storage()
    if storage:
        return storage.get_json(f"batches/{batch_id}/status.json")
    return read_json(local_status(batch_id))


def get_result(batch_id):
    storage = durable_storage()
    if storage:
        return storage.get_json(f"batches/{batch_id}/result.json")
    return read_json(RESULT_DIR / f"{batch_id}.json")


def materialize_source_file(batch_id, filename, destination):
    manifest = get_manifest(batch_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Batch not found.")
    document = next((item for item in manifest.get("documents", [])
                     if item.get("filename") == filename), None)
    if not document:
        raise HTTPException(status_code=404, detail="Source PDF not found in this batch.")
    storage = durable_storage()
    if storage:
        key = document.get("storage_key")
        if not key:
            raise HTTPException(status_code=404, detail="Source storage reference is missing.")
        storage.get_file(key, destination)
    else:
        relative = document.get("path")
        if not relative:
            raise HTTPException(status_code=404, detail="Source file path is missing.")
        source = (BASE_DIR / relative).resolve()
        allowed_root = (UPLOAD_DIR / batch_id).resolve()
        if allowed_root not in source.parents or not source.is_file():
            raise HTTPException(status_code=404, detail="Source PDF is unavailable.")
        shutil.copyfile(source, destination)
    return document


async def run_local_analysis(batch_id):
    batch_dir = UPLOAD_DIR / batch_id
    try:
        from services.ai_analyzer import analyze_batch_documents
        result = await analyze_batch_documents(batch_dir)
        result["job_status"] = "completed"
        result["completed_at"] = utc_now()
        atomic_json(RESULT_DIR / f"{batch_id}.json", result)
        set_batch_status(batch_id, "completed",
                         documents_processed=result.get("documents_processed", 0),
                         documents_total=result.get("documents_total", 0))
    except Exception as error:
        logger.exception("Local analysis failed for %s", batch_id)
        set_batch_status(batch_id, "failed", error=str(error))


def save_result(batch_id, result):
    storage = durable_storage()
    if storage:
        storage.put_json(f"batches/{batch_id}/result.json", result)
    else:
        atomic_json(RESULT_DIR / f"{batch_id}.json", result)


@app.get("/health")
async def health():
    return {"application": APP_NAME, "version": APP_VERSION, "status": "online", "timestamp": utc_now()}


@app.get("/api/status")
async def api_status():
    durable = durable_queue_enabled()
    return {
        "status": "ready",
        "analysis_mode": "celery_worker" if durable else "local_background_task",
        "durable_queue": durable,
        "durable_file_storage": bool(os.getenv("S3_BUCKET")),
        "worker_command": "celery -A worker.celery_app worker --loglevel=INFO --concurrency=1",
        "limits": {"files_per_batch": MAX_FILES_PER_BATCH,
                   "file_size_mb": MAX_FILE_SIZE_MB,
                   "batch_size_mb": MAX_BATCH_SIZE_MB},
        "export": "disabled_pending_approval_and_duplicate_safe_write"
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
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=413, detail=f"Maximum {MAX_FILES_PER_BATCH} PDFs per batch.")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    storage = durable_storage()
    uploaded_files, rejected_files = [], []
    seen_names, batch_bytes = set(), 0

    try:
        for index, uploaded in enumerate(files, start=1):
            original = Path(uploaded.filename or "").name
            if not original.lower().endswith(".pdf"):
                rejected_files.append({"filename": original, "reason": "Only PDF files are accepted."})
                await uploaded.close()
                continue

            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._") or f"document_{index}"
            filename = f"{stem}.pdf"
            if filename.lower() in seen_names:
                filename = f"{stem}_{index}.pdf"
            seen_names.add(filename.lower())
            destination = batch_dir / filename
            total, signature = 0, b""

            try:
                with destination.open("wb") as output:
                    while True:
                        chunk = await uploaded.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        if total == 0:
                            signature = chunk[:5]
                        total += len(chunk)
                        if total > MAX_FILE_SIZE_MB * 1024 * 1024:
                            raise ValueError(f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")
                        if batch_bytes + total > MAX_BATCH_SIZE_MB * 1024 * 1024:
                            raise ValueError(f"Batch exceeds {MAX_BATCH_SIZE_MB} MB limit.")
                        output.write(chunk)

                if not signature.startswith(b"%PDF"):
                    destination.unlink(missing_ok=True)
                    rejected_files.append({"filename": original, "reason": "Invalid PDF file signature."})
                    continue

                item = {"filename": filename, "original_filename": original, "size_bytes": total}
                if storage:
                    object_key = f"batches/{batch_id}/files/{filename}"
                    storage.put_file(object_key, destination)
                    item["storage_key"] = object_key
                    destination.unlink(missing_ok=True)
                else:
                    item["path"] = str(destination.relative_to(BASE_DIR))
                uploaded_files.append(item)
                batch_bytes += total
            except Exception as error:
                destination.unlink(missing_ok=True)
                rejected_files.append({"filename": original, "reason": str(error)})
            finally:
                await uploaded.close()

        if not uploaded_files:
            raise HTTPException(status_code=400, detail={
                "message": "No valid PDFs were uploaded.", "rejected_files": rejected_files
            })

        manifest = {
            "batch_id": batch_id, "created_at": utc_now(),
            "document_count": len(uploaded_files), "total_size_bytes": batch_bytes,
            "documents": uploaded_files, "rejected_files": rejected_files,
            "status": "uploaded"
        }
        save_manifest(batch_id, manifest)
        return {"message": "Documents uploaded successfully.", **manifest}
    except Exception:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise


@app.get("/api/batches")
async def list_batches():
    storage = durable_storage()
    batches = []
    if storage:
        for key in storage.list_keys("batches/"):
            if key.endswith("/manifest.json"):
                manifest = storage.get_json(key)
                if manifest:
                    state = storage.get_json(f"batches/{manifest['batch_id']}/status.json")
                    if state:
                        manifest["status"] = state.get("status", manifest.get("status"))
                    batches.append(manifest)
    else:
        for batch_dir in sorted(UPLOAD_DIR.iterdir(), reverse=True):
            if batch_dir.is_dir():
                manifest = read_json(batch_dir / "batch.json")
                if manifest:
                    state = read_json(local_status(batch_dir.name))
                    if state:
                        manifest["status"] = state.get("status", manifest.get("status"))
                    batches.append(manifest)
    batches.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"batches": batches}


@app.post("/api/analyze/{batch_id}")
async def analyze_batch(batch_id: str, background_tasks: BackgroundTasks):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    manifest = get_manifest(batch_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Batch not found.")
    existing = get_status(batch_id) or {}
    if existing.get("status") in ("queued", "processing"):
        return {"batch_id": batch_id, "status": existing["status"], "message": "Analysis is already queued or running."}
    if get_result(batch_id):
        return {"batch_id": batch_id, "status": "completed", "message": "Analysis already completed."}

    if durable_queue_enabled():
        try:
            from worker import analyze_batch_task
            set_batch_status(batch_id, "queued", queued_at=utc_now())
            task = analyze_batch_task.delay(batch_id)
            return {"batch_id": batch_id, "status": "queued", "task_id": task.id}
        except Exception as error:
            logger.exception("Unable to enqueue durable job")
            set_batch_status(batch_id, "queue_failed", error=str(error))
            raise HTTPException(status_code=503, detail="The durable worker queue is unavailable. Check REDIS_URL and worker health.")
    else:
        set_batch_status(batch_id, "queued", queued_at=utc_now(),
                         warning="Local fallback: job can be interrupted by service restart.")
        background_tasks.add_task(run_local_analysis, batch_id)
        return {"batch_id": batch_id, "status": "queued",
                "warning": "Local background mode is not restart-safe. Configure REDIS_URL and S3_BUCKET for durable processing."}


@app.get("/api/source/{batch_id}/{filename}/page/{page_number}")
async def preview_source_page(batch_id: str, filename: str, page_number: int):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,180}\.pdf", filename, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid PDF filename.")
    if page_number < 1 or page_number > 500:
        raise HTTPException(status_code=400, detail="Page number must be between 1 and 500.")
    import fitz
    with tempfile.TemporaryDirectory(prefix="6ws-preview-") as temp_dir:
        source_path = Path(temp_dir) / "source.pdf"
        materialize_source_file(batch_id, filename, source_path)
        try:
            with fitz.open(source_path) as pdf:
                if page_number > len(pdf):
                    raise HTTPException(status_code=404, detail="PDF page does not exist.")
                pix = pdf.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
                image_bytes = pix.tobytes("png")
        except HTTPException:
            raise
        except Exception as error:
            logger.exception("Unable to render source PDF page")
            raise HTTPException(status_code=422, detail="Unable to render this PDF page.") from error
    return Response(content=image_bytes, media_type="image/png",
                    headers={"Cache-Control": "private, no-store"})


@app.get("/api/results/{batch_id}")
async def get_analysis_status(batch_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    result = get_result(batch_id)
    if result:
        return {"status": "completed", "result": result}
    state = get_status(batch_id)
    if state:
        return state
    if not get_manifest(batch_id):
        raise HTTPException(status_code=404, detail="Batch not found.")
    return {"batch_id": batch_id, "status": "uploaded"}


@app.put("/api/review/{batch_id}")
async def save_review(batch_id: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    current = get_result(batch_id)
    if not current:
        raise HTTPException(status_code=404, detail="Completed analysis result not found.")
    try:
        submitted = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Review payload must be valid JSON.")
    if not isinstance(submitted, dict):
        raise HTTPException(status_code=400, detail="Review payload must be a JSON object.")

    allowed_fields = {
        "trips": {"trip_date", "line_haul_trip_number", "driver_id", "driver_name",
            "plate_no", "fleet_type", "check_in_time_actual", "valid_time_circled",
            "docking_time", "docking_date_or_check_out_date", "check_out_time",
            "origin", "drop_offs", "destination", "with_backload",
            "demurrage_availment", "remarks", "late", "segments"},
        "toll_reimbursements": {"date", "line_haul_trip_number", "driver_id", "driver_name",
            "plate_no", "fleet_type", "origin", "destination", "rfid_toll_charges",
            "entry_point", "exit_point", "date_of_or", "tag_number", "amount"},
        "parking_reimbursements": {"trip_date", "line_haul_trip_number", "driver_id", "driver_name",
            "plate_no", "fleet_type", "origin", "destination", "vendor_name",
            "address", "date_of_or", "tag_number", "amount"},
        "passway_reimbursements": {"trip_date", "line_haul_trip_number", "driver_id", "driver_name",
            "plate_no", "fleet_type", "origin", "destination", "vendor_name",
            "address", "date_of_or", "tag_number", "amount"}
    }
    for section, fields in allowed_fields.items():
        existing_rows = current.get(section, [])
        submitted_rows = submitted.get(section, existing_rows)
        if not isinstance(submitted_rows, list) or len(submitted_rows) != len(existing_rows):
            raise HTTPException(status_code=400, detail=f"Invalid row collection: {section}.")
        for index, row in enumerate(submitted_rows):
            if not isinstance(row, dict):
                raise HTTPException(status_code=400, detail=f"Invalid row in {section}.")
            for field in fields:
                if field in row:
                    value = row[field]
                    if field == "drop_offs":
                        if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                            raise HTTPException(status_code=400, detail="drop_offs must be a list of text values.")
                    elif field == "segments":
                        if not isinstance(value, list) or not all(isinstance(segment, dict) for segment in value):
                            raise HTTPException(status_code=400, detail="segments must be a list of movement objects.")
                        existing_segments = current[section][index].get("segments", [])
                        if len(value) != len(existing_segments):
                            raise HTTPException(status_code=400, detail="Movement row count cannot be changed in review.")
                        for segment_index, segment in enumerate(value):
                            for segment_field in ("origin", "destination", "departure_time", "arrival_time"):
                                segment_value = segment.get(segment_field)
                                if segment_value is not None and not isinstance(segment_value, str):
                                    raise HTTPException(status_code=400, detail=f"Invalid movement field: {segment_field}.")
                                existing_segments[segment_index][segment_field] = segment_value
                    elif value is not None and not isinstance(value, (str, int, float, bool)):
                        raise HTTPException(status_code=400, detail=f"Invalid value for {field}.")
                    current[section][index][field] = value

    current["approval_status"] = "pending_review"
    current["overall_review_required"] = True
    current["review_saved_at"] = utc_now()
    save_result(batch_id, current)
    set_batch_status(batch_id, "review_saved", review_saved_at=current["review_saved_at"])
    return {"batch_id": batch_id, "status": "review_saved",
            "approval_status": "pending_review", "message": "Review edits saved. Export remains disabled."}


@app.post("/api/approve/{batch_id}")
async def approve_batch(batch_id: str):
    raise HTTPException(status_code=501, detail="Export remains disabled until persistent human approval and duplicate-safe Google Sheets writes are implemented.")


@app.get("/api")
async def api_information():
    return {
        "name": APP_NAME, "version": APP_VERSION,
        "workflow": ["Upload PDF documents", "Persist files and batch manifest",
                     "Queue analysis to worker when configured", "Analyze documents independently",
                     "Review all extracted records", "Export only after verified approval"]
    }
