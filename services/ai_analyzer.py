
import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from services.ocr_engine import extract_complete_pdf

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
EXTRACTION_WORKERS = max(1, min(int(os.getenv("PDF_EXTRACTION_WORKERS", "2")), 8))
_client = None


def get_ai_client():
    global _client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client


def classify_document(filename: str, text: str) -> str:
    combined = f"{filename} {text[:3000]}".lower()
    if "parking reimbursement" in combined:
        return "parking_reimbursement"
    if "toll reimbursement" in combined:
        return "toll_reimbursement"
    if any(term in combined for term in ["trip ticket", "trip summary", "line haul", "runsheet", "task information"]):
        return "trip_ticket"
    return "unknown"


def build_analysis_prompt(documents: list[dict]) -> str:
    document_text = json.dumps(documents, ensure_ascii=False, separators=(",", ":"))
    return f"""
You are the document analysis engine for 6WS Trip Encoder.
Analyze the supplied driver trip tickets and reimbursement documents.
Return valid JSON only using the schema below.

STRICT EVIDENCE RULES:
- Never invent dates, times, locations, trip numbers, amounts, driver names or vehicle details.
- Preserve source filename and PDF page number for every extracted fact.
- Separate trip tickets, toll reimbursements and parking reimbursements.
- Reconstruct movements using chronological evidence, not page order alone.
- Identify SOC origins, intermediate hubs, destinations, returns and possible backloads.
- Intermediate hubs belong in DROP OFFS. The farthest/last outbound hub before returning is DESTINATION.
- Return movements must be represented separately.
- A non-SOC to non-SOC movement is a possible backload only when the destination hub contains standalone token MFM, case-insensitive.
- SOC to MFM is outbound, not a backload.
- Do not merge documents for different drivers. If driver identity is uncertain, flag it.
- Do not approve or export. Set overall_review_required true when any uncertainty exists.
- If extracted text is incomplete or OCR failed, list the affected document/page in unresolved_items.

JSON SCHEMA:
{{
 "driver": {{"name": null, "confidence": "unknown", "evidence": []}},
 "documents": [{{"filename":"","document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | unknown","pages_used":[],"notes":[]}}],
 "trips": [{{"trip_ticket_number":null,"line_haul_trip_number":null,"trip_date":null,"origin":null,"drop_offs":[],"destination":null,"return_origin":null,"segments":[{{"sequence":1,"origin":null,"destination":null,"departure_time":null,"arrival_time":null,"movement_type":"outbound | return | backload | parking | unknown","source_pages":[],"source_filename":""}}],"check_in_time_actual":null,"docking_time":null,"check_out_time_actual":null,"review_flags":[],"confidence":"high | medium | low","source_filename":"","source_pages":[]}}],
 "toll_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"source_filename":"","review_flags":[]}}],
 "parking_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"source_filename":"","review_flags":[]}}],
 "unresolved_items":[],
 "overall_review_required":true
}}

SOURCE DOCUMENTS:
{document_text}
"""


def analyze_documents(documents: list[dict]) -> dict[str, Any]:
    client = get_ai_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_analysis_prompt(documents),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
    )
    if not response.text:
        raise RuntimeError("AI returned an empty analysis response.")
    try:
        result = json.loads(response.text)
    except json.JSONDecodeError as error:
        raise RuntimeError("AI response was not valid JSON.") from error
    result["analysis_model"] = GEMINI_MODEL
    result["approval_status"] = "pending_review"
    return result


def _extract_one(pdf_path: Path, original_name: str) -> dict:
    extraction = extract_complete_pdf(pdf_path)
    pages = [
        {"page_number": page["page_number"], "text": page.get("text", ""),
         "source": page.get("source"), "status": page.get("status")}
        for page in extraction["pages"]
    ]
    document_text = "\n".join(
        f"PAGE {page['page_number']}:\n{page['text']}" for page in pages
    )
    return {
        "filename": original_name,
        "stored_filename": pdf_path.name,
        "document_type": classify_document(original_name, document_text),
        "total_pages": extraction["total_pages"],
        "extraction_status": extraction["status"],
        "pages": pages
    }


async def analyze_batch_documents(batch_dir: str | Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    batch_file = batch_dir / "batch.json"
    metadata = json.loads(batch_file.read_text(encoding="utf-8")) if batch_file.exists() else {}
    file_records = metadata.get("documents") or []
    if file_records:
        pdf_records = [
            (batch_dir / record.get("stored_filename", ""), record.get("filename", "unknown.pdf"))
            for record in file_records
        ]
        pdf_records = [(path, name) for path, name in pdf_records if path.is_file()]
    else:
        pdf_records = [(path, path.name) for path in batch_dir.glob("*.pdf")]

    if not pdf_records:
        raise ValueError("No PDF documents found in this batch.")

    loop = asyncio.get_running_loop()
    extracted_documents = []
    extraction_errors = []
    # A bounded pool processes independent PDFs concurrently. Keep the cap low
    # for small Render instances; each PDF can itself use bounded OCR workers.
    with ThreadPoolExecutor(max_workers=min(EXTRACTION_WORKERS, len(pdf_records))) as pool:
        tasks = [
            loop.run_in_executor(pool, _extract_one, path, original_name)
            for path, original_name in pdf_records
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for (path, original_name), result in zip(pdf_records, results):
        if isinstance(result, Exception):
            logger.error("Failed to extract %s: %s", original_name, result)
            extraction_errors.append({"filename": original_name, "error": str(result)})
        else:
            extracted_documents.append(result)

    if not extracted_documents:
        raise RuntimeError("All PDF extractions failed.")

    analysis = await asyncio.to_thread(analyze_documents, extracted_documents)
    analysis["batch_id"] = batch_dir.name
    analysis["documents_processed"] = len(extracted_documents)
    analysis["documents_received"] = len(pdf_records)
    analysis["extraction_errors"] = extraction_errors
    analysis["approval_status"] = "pending_review"
    return analysis
