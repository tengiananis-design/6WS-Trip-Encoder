import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any
from google import genai
from google.genai import types
from services.ocr_engine import extract_complete_pdf
from services.driver_grouping import group_documents_by_driver, normalize_driver_name
from services.trip_processor import process_all_trips
from services.reimbursement_processor import process_reimbursements

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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
    if "parking reimbursement" in combined or "parking receipt" in combined:
        return "parking_reimbursement"
    if "toll reimbursement" in combined or "toll receipt" in combined:
        return "toll_reimbursement"
    if any(term in combined for term in ["trip ticket", "trip summary", "line haul", "runsheet", "task information"]):
        return "trip_ticket"
    return "unknown"

def build_analysis_prompt(documents: list[dict]) -> str:
    return f"""
You analyze ONE driver's PDF document at a time for 6WS Trip Encoder.
Use only evidence in the supplied pages. Never invent dates, times, locations, amounts, names or trip numbers.
Preserve source filename and page numbers. Classify as trip_ticket, toll_reimbursement, parking_reimbursement or unknown.
Reconstruct movement chronology from timestamps, not page order. Intermediate hubs go in drop_offs. The farthest/last outbound hub before returning is destination. Return movements are separate.
A non-SOC to non-SOC movement is a possible backload only if destination contains standalone token MFM (case-insensitive). SOC to MFM is outbound, not backload. Do not infer driver identity from filename alone.
Flag conflicts, missing fields, and uncertain relationships. Do not approve or export.
Return JSON only:
{{
 "driver": {{"name":null,"confidence":"unknown","evidence":[]}},
 "documents":[{{"filename":"","document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | unknown","pages_used":[],"notes":[]}}],
 "trips":[{{"trip_ticket_number":null,"line_haul_trip_number":null,"trip_date":null,"origin":null,"drop_offs":[],"destination":null,"return_origin":null,"segments":[{{"sequence":1,"origin":null,"destination":null,"departure_time":null,"arrival_time":null,"movement_type":"outbound | return | backload | parking | unknown","source_pages":[]}}],"check_in_time_actual":null,"docking_time":null,"check_out_time_actual":null,"review_flags":[],"confidence":"high | medium | low"}}],
 "toll_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"review_flags":[]}}],
 "parking_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"review_flags":[]}}],
 "unresolved_items":[]
}}
SOURCE DOCUMENT:
{json.dumps(documents, ensure_ascii=False)}
"""

def analyze_documents(documents: list[dict]) -> dict[str, Any]:
    response = get_ai_client().models.generate_content(
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
    if not isinstance(result, dict):
        raise RuntimeError("AI response must be a JSON object.")
    return result

def _driver_name(result: dict) -> str:
    driver = result.get("driver") or {}
    return driver.get("name") if isinstance(driver, dict) else str(driver or "")

async def analyze_batch_documents(batch_dir: str | Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    pdf_files = sorted(p for p in batch_dir.glob("*.pdf") if p.is_file())
    if not pdf_files:
        raise ValueError("No PDF documents found in this batch.")

    per_document_results = []
    extraction_errors = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        try:
            extraction = await asyncio.to_thread(extract_complete_pdf, pdf_path)
            pages = extraction.get("pages", [])
            if extraction.get("status") == "ocr_not_installed":
                raise RuntimeError(extraction.get("message", "OCR is unavailable."))
            if any(page.get("status") == "failed" for page in pages):
                logger.warning("Some pages failed OCR in %s", pdf_path.name)
            prompt_doc = [{
                "filename": pdf_path.name,
                "document_type_hint": classify_document(pdf_path.name, "\n".join(p.get("text","") for p in pages)),
                "total_pages": extraction.get("total_pages"),
                "pages": pages
            }]
            analysis = await asyncio.to_thread(analyze_documents, prompt_doc)
            docs = analysis.get("documents") or [{
                "filename": pdf_path.name,
                "document_type": classify_document(pdf_path.name, "\n".join(p.get("text","") for p in pages)),
                "pages_used": [p.get("page_number") for p in pages],
                "notes": []
            }]
            for doc in docs:
                doc.setdefault("filename", pdf_path.name)
                doc["driver"] = analysis.get("driver") or {}
            analysis["documents"] = docs
            analysis["source_filename"] = pdf_path.name
            analysis["extraction_status"] = extraction.get("status")
            analysis["page_extraction_review_required"] = any(page.get("status") == "failed" for page in pages)
            analysis["document_type_review_required"] = any(doc.get("document_type") == "unknown" for doc in docs)
            per_document_results.append(analysis)
        except Exception as error:
            logger.exception("Failed to process %s", pdf_path.name)
            extraction_errors.append({"filename": pdf_path.name, "error": str(error)})

    if not per_document_results:
        raise RuntimeError("No PDF documents could be analyzed successfully.")

    # Driver grouping uses exact normalized identity only. Unknown and conflicting
    # identities remain in a separate review queue; no fuzzy auto-merging.
    flat_docs = [doc for item in per_document_results for doc in item.get("documents", [])]
    grouping = group_documents_by_driver(flat_docs)

    by_driver = {}
    for item in per_document_results:
        name = _driver_name(item)
        key = normalize_driver_name(name)
        if not key:
            continue
        bucket = by_driver.setdefault(key, {
            "driver": item.get("driver") or {"name": name},
            "trips": [], "toll_reimbursements": [], "parking_reimbursements": [],
            "documents": [], "unresolved_items": []
        })
        bucket["trips"].extend(item.get("trips", []))
        bucket["toll_reimbursements"].extend(item.get("toll_reimbursements", []))
        bucket["parking_reimbursements"].extend(item.get("parking_reimbursements", []))
        bucket["documents"].extend(item.get("documents", []))
        bucket["unresolved_items"].extend(item.get("unresolved_items", []))

    driver_results = []
    for key, bucket in by_driver.items():
        trip_result = process_all_trips({"driver": bucket["driver"], "trips": bucket["trips"]})
        expense_result = process_reimbursements({
            "driver": bucket["driver"],
            "toll_reimbursements": bucket["toll_reimbursements"],
            "parking_reimbursements": bucket["parking_reimbursements"]
        })
        driver_results.append({
            "driver": bucket["driver"],
            "documents": bucket["documents"],
            "trips": trip_result["trips"],
            "toll_reimbursements": expense_result["toll_reimbursements"],
            "parking_reimbursements": expense_result["parking_reimbursements"],
            "unresolved_items": bucket["unresolved_items"],
            "status": "pending_review"
        })

    overall_review = bool(extraction_errors or grouping["unidentified_documents"] or grouping["review_items"])
    overall_review = overall_review or any(item.get("page_extraction_review_required") or item.get("document_type_review_required") for item in per_document_results)
    overall_review = overall_review or any(
        d.get("unresolved_items") or any(t.get("review_flags") for t in d.get("trips", []))
        or any(x.get("review_flags") for x in d.get("toll_reimbursements", []) + d.get("parking_reimbursements", []))
        for d in driver_results
    )
    return {
        "batch_id": batch_dir.name,
        "analysis_model": GEMINI_MODEL,
        "documents_processed": len(per_document_results),
        "documents_total": len(pdf_files),
        "extraction_errors": extraction_errors,
        "driver_count": len(driver_results),
        "drivers": driver_results,
        "grouping": {
            "driver_count": grouping["driver_count"],
            "unidentified_documents": grouping["unidentified_documents"],
            "review_items": grouping["review_items"]
        },
        "trips": [trip for driver in driver_results for trip in driver["trips"]],
        "toll_reimbursements": [row for driver in driver_results for row in driver["toll_reimbursements"]],
        "parking_reimbursements": [row for driver in driver_results for row in driver["parking_reimbursements"]],
        "unresolved_items": [item for driver in driver_results for item in driver["unresolved_items"]],
        "overall_review_required": True,  # Manual review/export workflow is not yet implemented.
        "approval_status": "pending_review"
    }
