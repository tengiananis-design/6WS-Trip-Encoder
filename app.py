import os
import json
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, UploadFile, File, HTTPException
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

MAX_FILES_PER_BATCH = int(os.getenv("MAX_FILES_PER_BATCH", "200"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
CHUNK_SIZE = 1024 * 1024
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(APP_NAME)

app = FastAPI(title=APP_NAME, version=APP_VERSION,
              description="AI-assisted trip and reimbursement encoding system")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

def utc_now():
    return datetime.now(timezone.utc).isoformat()

@app.get("/health")
async def health():
    return {"application": APP_NAME, "version": APP_VERSION, "status": "online", "timestamp": utc_now()}

@app.get("/api/status")
async def application_status():
    return {
        "status": "ready",
        "pdf_processing": "streamed_to_disk",
        "max_files_per_batch": MAX_FILES_PER_BATCH,
        "max_file_size_mb": MAX_FILE_SIZE_MB,
        "ai_analysis": "per_document",
        "google_sheets": "requires_credentials_and_spreadsheet_configuration"
    }

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": APP_NAME, "version": APP_VERSION})

@app.post("/api/upload")
async def upload_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files selected.")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=413, detail=f"Maximum {MAX_FILES_PER_BATCH} PDFs per batch.")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    uploaded_files, rejected_files = [], []
    seen_names = set()

    try:
        for index, uploaded in enumerate(files, start=1):
            original = Path(uploaded.filename or "").name
            if not original.lower().endswith(".pdf"):
                rejected_files.append({"filename": original, "reason": "Only PDF files are accepted."})
                await uploaded.close()
                continue

            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._") or f"document_{index}"
            safe_name = safe_stem + ".pdf"
            if safe_name.lower() in seen_names:
                safe_name = f"{safe_stem}_{index}.pdf"
            seen_names.add(safe_name.lower())
            destination = batch_dir / safe_name
            total = 0
            signature = b""

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
                        output.write(chunk)

                if not signature.startswith(b"%PDF"):
                    destination.unlink(missing_ok=True)
                    rejected_files.append({"filename": original, "reason": "Invalid PDF file signature."})
                    continue

                uploaded_files.append({
                    "filename": safe_name,
                    "original_filename": original,
                    "size_bytes": total,
                    "path": str(destination.relative_to(BASE_DIR))
                })
            except Exception as error:
                destination.unlink(missing_ok=True)
                rejected_files.append({"filename": original, "reason": str(error)})
            finally:
                await uploaded.close()

        if not uploaded_files:
            raise HTTPException(status_code=400, detail={"message": "No valid PDFs were uploaded.", "rejected_files": rejected_files})

        batch_information = {
            "batch_id": batch_id,
            "created_at": utc_now(),
            "document_count": len(uploaded_files),
            "documents": uploaded_files,
            "rejected_files": rejected_files,
            "status": "uploaded"
        }
        (batch_dir / "batch.json").write_text(json.dumps(batch_information, indent=2), encoding="utf-8")
        return {"message": "Documents uploaded successfully.", **batch_information}
    except Exception:
        # Keep successfully uploaded files only when the batch manifest was written.
        if not (batch_dir / "batch.json").exists():
            import shutil
            shutil.rmtree(batch_dir, ignore_errors=True)
        raise

@app.get("/api/batches")
async def list_batches():
    batches = []
    for batch_dir in sorted(UPLOAD_DIR.iterdir(), reverse=True):
        batch_file = batch_dir / "batch.json"
        if batch_dir.is_dir() and batch_file.exists():
            try:
                batches.append(json.loads(batch_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                logger.warning("Unable to read batch manifest: %s", batch_file)
    return {"batches": batches}

@app.post("/api/analyze/{batch_id}")
async def analyze_batch(batch_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    batch_dir = UPLOAD_DIR / batch_id
    if not batch_dir.is_dir() or not (batch_dir / "batch.json").exists():
        raise HTTPException(status_code=404, detail="Batch not found.")
    result_file = RESULT_DIR / f"{batch_id}.json"
    try:
        from services.ai_analyzer import analyze_batch_documents
        result = await analyze_batch_documents(batch_dir)
        temp_file = result_file.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(result_file)
        return {"message": "Analysis completed.", "batch_id": batch_id, "result": result}
    except Exception as error:
        logger.exception("Analysis failed for batch %s", batch_id)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(error)}")

@app.get("/api/results/{batch_id}")
async def get_analysis_result(batch_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise HTTPException(status_code=400, detail="Invalid batch ID.")
    result_file = RESULT_DIR / f"{batch_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="Analysis result not found.")
    return json.loads(result_file.read_text(encoding="utf-8"))

@app.post("/api/approve/{batch_id}")
async def approve_batch(batch_id: str):
    raise HTTPException(status_code=501, detail="Export is disabled until the TENGS column mapping and approval workflow are verified.")

@app.get("/api")
async def api_information():
    return {"name": APP_NAME, "version": APP_VERSION, "workflow": [
        "Upload PDF documents", "Extract each PDF independently", "Analyze documents in bounded requests",
        "Group only exact normalized driver identities", "Normalize trip and reimbursement records",
        "Review all extracted information", "Export only after verified approval and sheet mapping"
    ]}

@app.on_event("startup")
async def startup_event():
    logger.info("%s started successfully.", APP_NAME)
