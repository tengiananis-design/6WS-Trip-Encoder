
import os
import json
import logging
import re
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

APP_NAME = "6WS Trip Encoder"
APP_VERSION = "1.1.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
for directory in (DATA_DIR, UPLOAD_DIR, RESULT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "150"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(APP_NAME)
app = FastAPI(title=APP_NAME, version=APP_VERSION, description="AI-assisted trip and reimbursement encoding system")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def safe_batch_dir(batch_id: str) -> Path:
    if not batch_id or Path(batch_id).name != batch_id or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    return UPLOAD_DIR / batch_id


def read_batch(batch_dir: Path) -> dict:
    batch_file = batch_dir / "batch.json"
    if not batch_dir.is_dir() or not batch_file.exists():
        raise HTTPException(status_code=404, detail="Batch not found.")
    try:
        return json.loads(batch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="Batch metadata is unreadable.")


def write_batch(batch_dir: Path, batch: dict) -> None:
    temp_file = batch_dir / "batch.json.tmp"
    temp_file.write_text(json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(batch_dir / "batch.json")


def create_batch() -> dict:
    batch_id = uuid.uuid4().hex
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    batch = {
        "batch_id": batch_id,
        "created_at": datetime.utcnow().isoformat(),
        "document_count": 0,
        "documents": [],
        "status": "created"
    }
    write_batch(batch_dir, batch)
    return batch


@app.get("/health")
async def health():
    return {"application": APP_NAME, "version": APP_VERSION, "status": "online", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/status")
async def application_status():
    return {
        "status": "online",
        "pdf_processing": "available",
        "ai_analysis": "configured" if os.getenv("GEMINI_API_KEY") else "missing_api_key",
        "google_sheets": "configured" if (os.getenv("GOOGLE_SHEETS_ID") or os.getenv("GOOGLE_SPREADSHEET_ID")) else "missing_spreadsheet_id",
        "max_upload_files_per_batch": MAX_UPLOAD_FILES,
        "max_pdf_size_mb": MAX_UPLOAD_SIZE_MB
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "app_name": APP_NAME, "version": APP_VERSION},
    )


@app.post("/api/batches")
async def start_batch():
    return create_batch()


@app.post("/api/upload")
async def upload_documents(files: list[UploadFile] = File(...), batch_id: Optional[str] = Form(default=None)):
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files selected.")

    if batch_id:
        batch_dir = safe_batch_dir(batch_id)
        batch = read_batch(batch_dir)
    else:
        batch = create_batch()
        batch_id = batch["batch_id"]
        batch_dir = UPLOAD_DIR / batch_id

    if batch.get("document_count", 0) + len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"This batch supports up to {MAX_UPLOAD_FILES} PDF files.")

    uploaded_files = []
    rejected_files = []

    for uploaded_file in files:
        original_name = Path(uploaded_file.filename or "document.pdf").name
        if not original_name.lower().endswith(".pdf"):
            rejected_files.append({"filename": original_name, "reason": "Only PDF files are supported."})
            await uploaded_file.close()
            continue

        stored_name = f"{uuid.uuid4().hex[:12]}_{original_name}"
        destination = batch_dir / stored_name
        temporary = batch_dir / f"{stored_name}.part"
        total_size = 0
        first_bytes = b""

        try:
            with temporary.open("wb") as output:
                while True:
                    chunk = await uploaded_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if not first_bytes:
                        first_bytes = chunk[:8]
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_SIZE_BYTES:
                        raise HTTPException(status_code=413, detail=f"{original_name} exceeds the {MAX_UPLOAD_SIZE_MB} MB per-file limit.")
                    output.write(chunk)

            if not first_bytes.startswith(b"%PDF-"):
                temporary.unlink(missing_ok=True)
                rejected_files.append({"filename": original_name, "reason": "File is not a valid PDF."})
                continue

            temporary.replace(destination)
            uploaded_files.append({
                "filename": original_name,
                "stored_filename": stored_name,
                "size_bytes": total_size,
                "path": str(destination.relative_to(BASE_DIR))
            })
        except HTTPException:
            temporary.unlink(missing_ok=True)
            raise
        except Exception:
            temporary.unlink(missing_ok=True)
            logger.exception("Upload failed for %s", original_name)
            rejected_files.append({"filename": original_name, "reason": "Upload could not be saved."})
        finally:
            await uploaded_file.close()

    if not uploaded_files and not batch.get("documents"):
        raise HTTPException(status_code=400, detail="No valid PDF files were uploaded.")

    batch["documents"].extend(uploaded_files)
    batch["document_count"] = len(batch["documents"])
    batch["status"] = "uploaded" if batch["document_count"] else "created"
    batch["updated_at"] = datetime.utcnow().isoformat()
    write_batch(batch_dir, batch)

    return {
        "message": "Upload chunk processed.",
        "batch_id": batch_id,
        "uploaded_count": len(uploaded_files),
        "total_document_count": batch["document_count"],
        "documents": uploaded_files,
        "rejected_files": rejected_files,
        "status": batch["status"]
    }


@app.get("/api/batches")
async def list_batches():
    batches = []
    for batch_dir in sorted(UPLOAD_DIR.iterdir(), reverse=True):
        batch_file = batch_dir / "batch.json"
        if batch_dir.is_dir() and batch_file.exists():
            try:
                batches.append(json.loads(batch_file.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                logger.warning("Invalid batch file: %s", batch_file)
    return {"batches": batches}


def analysis_status_path(batch_id: str) -> Path:
    return RESULT_DIR / f"{batch_id}.status.json"


def write_analysis_status(batch_id: str, status: dict) -> None:
    status_file = analysis_status_path(batch_id)
    temporary = RESULT_DIR / f"{batch_id}.status.json.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    temporary.replace(status_file)


async def run_analysis_task(batch_id: str) -> None:
    batch_dir = safe_batch_dir(batch_id)
    try:
        from services.ai_analyzer import analyze_batch_documents
        write_analysis_status(batch_id, {
            "batch_id": batch_id, "status": "processing",
            "message": "Extracting PDFs and analyzing driver packages."
        })
        result = await analyze_batch_documents(batch_dir)
        result_file = RESULT_DIR / f"{batch_id}.json"
        temporary = RESULT_DIR / f"{batch_id}.json.tmp"
        temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(result_file)
        write_analysis_status(batch_id, {
            "batch_id": batch_id, "status": "completed",
            "message": "Analysis completed.",
            "documents_processed": result.get("documents_processed", 0),
            "driver_count": result.get("driver_count", 0)
        })
    except Exception as error:
        logger.exception("Analysis failed for batch %s", batch_id)
        write_analysis_status(batch_id, {
            "batch_id": batch_id, "status": "failed",
            "message": str(error)
        })


@app.post("/api/analyze/{batch_id}", status_code=202)
async def analyze_batch(batch_id: str, background_tasks: BackgroundTasks):
    batch_dir = safe_batch_dir(batch_id)
    read_batch(batch_dir)
    status_file = analysis_status_path(batch_id)
    if status_file.exists():
        current = json.loads(status_file.read_text(encoding="utf-8"))
        if current.get("status") in ("queued", "processing"):
            return current

    # A deliberate rerun must never leave an older result visible while the
    # new analysis is queued or running. Remove stale output before scheduling.
    result_file = RESULT_DIR / f"{batch_id}.json"
    result_file.unlink(missing_ok=True)
    write_analysis_status(batch_id, {
        "batch_id": batch_id, "status": "queued",
        "message": "Analysis queued.",
        "queued_at": datetime.utcnow().isoformat()
    })
    background_tasks.add_task(run_analysis_task, batch_id)
    return {
        "batch_id": batch_id, "status": "queued",
        "message": "Analysis queued. You can safely keep this page open while processing continues."
    }


@app.get("/api/analyze/{batch_id}/status")
async def get_analysis_status(batch_id: str):
    safe_batch_dir(batch_id)
    status_file = analysis_status_path(batch_id)
    if not status_file.exists():
        result_file = RESULT_DIR / f"{batch_id}.json"
        if result_file.exists():
            result = json.loads(result_file.read_text(encoding="utf-8"))
            return {
                "batch_id": batch_id, "status": "completed",
                "documents_processed": result.get("documents_processed", 0),
                "driver_count": result.get("driver_count", 0)
            }
        raise HTTPException(status_code=404, detail="No analysis job has been started for this batch.")
    return json.loads(status_file.read_text(encoding="utf-8"))


@app.get("/api/results/{batch_id}")
async def get_analysis_result(batch_id: str):
    safe_batch_dir(batch_id)
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="Analysis result not found.")
    return json.loads(result_file.read_text(encoding="utf-8"))


@app.put("/api/results/{batch_id}")
async def save_analysis_result(batch_id: str, result: dict):
    safe_batch_dir(batch_id)
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="Analysis result not found.")
    # Validate the complete review state on the server, not only a browser boolean.
    if result.get("approval_status") == "approved":
        from services.approval import approval_issues
        issues = approval_issues(result)
        if issues:
            raise HTTPException(status_code=409, detail=" ".join(issues))
    result["batch_id"] = batch_id
    temporary = RESULT_DIR / f"{batch_id}.json.tmp"
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(result_file)
    return {"message": "Review state saved.", "approval_status": result.get("approval_status", "pending_review")}


@app.post("/api/approve/{batch_id}")
async def approve_batch(batch_id: str):
    safe_batch_dir(batch_id)
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="No analysis result available for approval.")
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
        from services.google_sheets import export_approved_batch
        from services.approval import approval_issues
        if result.get("approval_status") != "approved":
            raise HTTPException(status_code=409, detail="Approval must be explicitly recorded after human review before export.")
        issues = approval_issues(result)
        if issues:
            raise HTTPException(status_code=409, detail=" ".join(issues))
        export_result = await export_approved_batch(batch_id=batch_id, result=result)
        return {"message": "Approved batch exported.", "batch_id": batch_id, "export": export_result}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(error)}")


@app.get("/api")
async def api_information():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "workflow": [
            "Create one batch",
            "Upload PDFs in small chunks",
            "Extract digital text and OCR scanned pages concurrently",
            "Analyze trip and reimbursement information",
            "Review extracted information",
            "Approve results",
            "Export to Google Sheets"
        ]
    }


@app.on_event("startup")
async def startup_event():
    # FastAPI BackgroundTasks live only inside this process. If Render restarts
    # while a batch is queued or processing, that task is gone; leaving the old
    # status untouched would make the UI poll forever and block a retry.
    recovered = 0
    for status_file in RESULT_DIR.glob("*.status.json"):
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
            if status.get("status") in ("queued", "processing"):
                status.update({
                    "status": "failed",
                    "message": (
                        "Analysis was interrupted by a server restart. "
                        "Please select Analyze Documents to retry this batch."
                    ),
                    "interrupted_at": datetime.utcnow().isoformat()
                })
                temporary = status_file.with_suffix(status_file.suffix + ".tmp")
                temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
                temporary.replace(status_file)
                recovered += 1
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not recover analysis status file %s", status_file)
    logger.info("%s started successfully. Recovered %s interrupted analysis job(s).", APP_NAME, recovered)
