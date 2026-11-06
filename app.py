
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

APP_NAME = "6WS Trip Encoder"
APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"

for directory in (DATA_DIR, UPLOAD_DIR, RESULT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(APP_NAME)

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="AI-assisted trip and reimbursement encoding system"
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

# --------------------------------------------------
# APPLICATION HEALTH
# --------------------------------------------------

@app.get("/health")
async def health():
    return {
        "application": APP_NAME,
        "version": APP_VERSION,
        "status": "online",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/status")
async def application_status():
    return {
        "status": "ready",
        "pdf_processing": "configured_by_service",
        "ai_analysis": "configured_by_environment",
        "google_sheets": "configured_by_environment"
    }


# --------------------------------------------------
# MAIN DASHBOARD
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "version": APP_VERSION
        }
    )


# --------------------------------------------------
# PDF UPLOAD
# --------------------------------------------------

@app.post("/api/upload")
async def upload_documents(
    files: list[UploadFile] = File(...)
):
    if not files:
        raise HTTPException(
            status_code=400,
            detail="No PDF files selected."
        )

    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    batch_dir = UPLOAD_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    uploaded_files = []

    for uploaded_file in files:
        filename = Path(uploaded_file.filename or "").name

        if not filename.lower().endswith(".pdf"):
            continue

        destination = batch_dir / filename

        try:
            contents = await uploaded_file.read()

            if not contents.startswith(b"%PDF"):
                continue

            destination.write_bytes(contents)

            uploaded_files.append({
                "filename": filename,
                "size_bytes": len(contents),
                "path": str(destination.relative_to(BASE_DIR))
            })

        finally:
            await uploaded_file.close()

    if not uploaded_files:
        raise HTTPException(
            status_code=400,
            detail="No valid PDF files were uploaded."
        )

    batch_information = {
        "batch_id": batch_id,
        "created_at": datetime.utcnow().isoformat(),
        "document_count": len(uploaded_files),
        "documents": uploaded_files,
        "status": "uploaded"
    }

    batch_file = batch_dir / "batch.json"
    batch_file.write_text(
        json.dumps(batch_information, indent=2),
        encoding="utf-8"
    )

    return {
        "message": "Documents uploaded successfully.",
        **batch_information
    }


# --------------------------------------------------
# DOCUMENT INVENTORY
# --------------------------------------------------

@app.get("/api/batches")
async def list_batches():
    batches = []

    for batch_dir in sorted(UPLOAD_DIR.iterdir(), reverse=True):
        batch_file = batch_dir / "batch.json"

        if batch_dir.is_dir() and batch_file.exists():
            try:
                batches.append(
                    json.loads(batch_file.read_text(encoding="utf-8"))
                )
            except json.JSONDecodeError:
                logger.warning("Invalid batch file: %s", batch_file)

    return {"batches": batches}


# --------------------------------------------------
# AI DOCUMENT ANALYSIS
# --------------------------------------------------

@app.post("/api/analyze/{batch_id}")
async def analyze_batch(batch_id: str):
    batch_dir = UPLOAD_DIR / batch_id

    if not batch_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Batch not found."
        )

    try:
        # Implemented in services/ai_analyzer.py
        from services.ai_analyzer import analyze_batch_documents

        result = await analyze_batch_documents(batch_dir)

        result_file = RESULT_DIR / f"{batch_id}.json"
        result_file.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return {
            "message": "Analysis completed.",
            "batch_id": batch_id,
            "result": result
        }

    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="AI analyzer service has not been installed yet."
        )
    except Exception as error:
        logger.exception("Analysis failed")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(error)}"
        )


# --------------------------------------------------
# REVIEW ANALYSIS
# --------------------------------------------------

@app.get("/api/results/{batch_id}")
async def get_analysis_result(batch_id: str):
    result_file = RESULT_DIR / f"{batch_id}.json"

    if not result_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Analysis result not found."
        )

    return json.loads(result_file.read_text(encoding="utf-8"))


# --------------------------------------------------
# APPROVE AND EXPORT
# --------------------------------------------------

@app.post("/api/approve/{batch_id}")
async def approve_batch(batch_id: str):
    result_file = RESULT_DIR / f"{batch_id}.json"

    if not result_file.exists():
        raise HTTPException(
            status_code=404,
            detail="No analysis result available for approval."
        )

    try:
        result = json.loads(
            result_file.read_text(encoding="utf-8")
        )

        # Implemented in services/google_sheets.py
        from services.google_sheets import export_approved_batch

        export_result = await export_approved_batch(
            batch_id=batch_id,
            result=result
        )

        return {
            "message": "Approved batch exported.",
            "batch_id": batch_id,
            "export": export_result
        }

    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Google Sheets service has not been installed yet."
        )
    except Exception as error:
        logger.exception("Export failed")
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(error)}"
        )


# --------------------------------------------------
# ROOT INFORMATION
# --------------------------------------------------

@app.get("/api")
async def api_information():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "workflow": [
            "Upload driver PDFs",
            "Group documents by driver",
            "Extract PDF text and scanned pages",
            "Analyze trips and reimbursements",
            "Review extracted information",
            "Approve results",
            "Export to Google Sheets"
        ]
    }


# --------------------------------------------------
# STARTUP
# --------------------------------------------------

@app.on_event("startup")
async def startup_event():
    logger.info("%s started successfully.", APP_NAME)
