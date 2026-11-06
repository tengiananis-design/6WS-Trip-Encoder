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
    combined = f"{filename} {text[:5000]}".lower()
    if any(term in combined for term in ("passway reimbursement", "passway receipt", "passway")):
        return "passway_reimbursement"
    if any(term in combined for term in ("parking reimbursement", "parking receipt", "parking fee")):
        return "parking_reimbursement"
    if any(term in combined for term in ("toll reimbursement", "toll receipt", "rfid toll", "easytrip", "autosweep")):
        return "toll_reimbursement"
    if any(term in combined for term in ("trip ticket", "trip summary", "line haul", "linehaul", "runsheet", "task information")):
        return "trip_ticket"
    return "unknown"


def build_analysis_prompt(documents: list[dict]) -> str:
    return f"""
You extract structured evidence from ONE PDF document for 6WS Trip Encoder.
Do not assume the PDF is complete or belongs to a driver based only on its filename.
Never invent values. Use null when a field is absent. Keep IDs, plate numbers, tag numbers,
trip numbers and OR numbers as strings to preserve leading zeros. Preserve source page numbers.
Classify the PDF as trip_ticket, toll_reimbursement, parking_reimbursement,
passway_reimbursement, or unknown.

TRIP RULES:
- Reconstruct movements chronologically from explicit timestamps, not PDF page order.
- Record each evidenced movement as a separate segment with source_pages.
- For an outbound route, intermediate hubs belong in drop_offs; destination is the farthest/last
  outbound hub before the return toward the originating SOC.
- A return movement is a separate segment/row.
- A non-SOC to non-SOC movement is a backload only when the destination contains standalone
  token MFM, case-insensitive. SOC to MFM is outbound, never backload.
- Do not infer a missing movement, trip number, driver, vehicle or time.
- Capture actual check-in time, circled valid time, docking time/date, and actual check-out time
  separately whenever shown.
- Capture driver ID, driver name, plate number and fleet type when explicitly shown.
- If multiple trips appear, return separate trip objects.
- If a trip ticket has a route sequence that conflicts with timestamps, flag it.

TOLL FIELDS for the TOLL worksheet:
date, line_haul_trip_number, driver_id, driver_name, plate_no, fleet_type, origin,
destination, rfid_toll_charges (provider/type), entry_point, exit_point, date_of_or,
tag_number, amount, source_pages, review_flags.

PARKING and PASSWAY FIELDS for their separate worksheets:
trip_date, line_haul_trip_number, driver_id, driver_name, plate_no, fleet_type,
origin, destination, vendor_name, address, date_of_or, tag_number, amount,
source_pages, review_flags.

For expense documents, return one record per receipt/charge. Do not merge separate receipts.
If a receipt contains several toll passages, return one toll record per passage where the
entry/exit/tag/amount are separately stated. Flag incomplete or ambiguous records.

Return JSON only in this shape:
{{
 "driver": {{"name":null,"id":null,"confidence":"unknown","evidence":[]}},
 "documents":[{{"filename":"","document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | passway_reimbursement | unknown","pages_used":[],"notes":[]}}],
 "trips":[{{
   "trip_ticket_number":null,"line_haul_trip_number":null,"trip_date":null,
   "driver_id":null,"driver_name":null,"plate_no":null,"fleet_type":null,
   "check_in_time_actual":null,"valid_time_circled":null,"docking_time":null,
   "docking_date_or_check_out_date":null,"check_out_time":null,
   "origin":null,"drop_offs":[],"destination":null,"with_backload":null,
   "demurrage_availment":null,"remarks":null,"late":null,"return_origin":null,
   "segments":[{{"sequence":1,"origin":null,"destination":null,"departure_time":null,
      "arrival_time":null,"movement_type":"outbound | return | backload | parking | unknown",
      "source_pages":[]}}],
   "review_flags":[],"confidence":"high | medium | low"
 }}],
 "toll_reimbursements":[{{"date":null,"line_haul_trip_number":null,"driver_id":null,
   "driver_name":null,"plate_no":null,"fleet_type":null,"origin":null,"destination":null,
   "rfid_toll_charges":null,"entry_point":null,"exit_point":null,"date_of_or":null,
   "tag_number":null,"amount":null,"source_pages":[],"review_flags":[]}}],
 "parking_reimbursements":[{{"trip_date":null,"line_haul_trip_number":null,"driver_id":null,
   "driver_name":null,"plate_no":null,"fleet_type":null,"origin":null,"destination":null,
   "vendor_name":null,"address":null,"date_of_or":null,"tag_number":null,"amount":null,
   "source_pages":[],"review_flags":[]}}],
 "passway_reimbursements":[{{"trip_date":null,"line_haul_trip_number":null,"driver_id":null,
   "driver_name":null,"plate_no":null,"fleet_type":null,"origin":null,"destination":null,
   "vendor_name":null,"address":null,"date_of_or":null,"tag_number":null,"amount":null,
   "source_pages":[],"review_flags":[]}}],
 "unresolved_items":[]
}}

SOURCE PDF EXTRACTION:
{json.dumps(documents, ensure_ascii=False)}
"""


def analyze_documents(documents: list[dict]) -> dict[str, Any]:
    response = get_ai_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=build_analysis_prompt(documents),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
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
    for pdf_path in pdf_files:
        try:
            extraction = await asyncio.to_thread(extract_complete_pdf, pdf_path)
            pages = extraction.get("pages", [])
            if extraction.get("status") == "ocr_not_installed":
                raise RuntimeError(extraction.get("message", "OCR is unavailable."))
            page_text = "\n".join(p.get("text", "") for p in pages)
            hint = classify_document(pdf_path.name, page_text)
            prompt_doc = [{
                "filename": pdf_path.name,
                "document_type_hint": hint,
                "total_pages": extraction.get("total_pages"),
                "pages": pages
            }]
            analysis = await asyncio.to_thread(analyze_documents, prompt_doc)
            docs = analysis.get("documents") or [{
                "filename": pdf_path.name,
                "document_type": hint,
                "pages_used": [p.get("page_number") for p in pages],
                "notes": []
            }]
            for doc in docs:
                doc.setdefault("filename", pdf_path.name)
                doc["driver"] = analysis.get("driver") or {}
                doc.setdefault("document_type", hint)
            analysis["documents"] = docs
            analysis["source_filename"] = pdf_path.name
            analysis["extraction_status"] = extraction.get("status")
            analysis["page_extraction_review_required"] = any(p.get("status") == "failed" for p in pages)
            analysis["document_type_review_required"] = any(d.get("document_type") == "unknown" for d in docs)
            per_document_results.append(analysis)
        except Exception as error:
            logger.exception("Failed to process %s", pdf_path.name)
            extraction_errors.append({"filename": pdf_path.name, "error": str(error)})

    if not per_document_results:
        raise RuntimeError("No PDF documents could be analyzed successfully.")

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
            "passway_reimbursements": [], "documents": [], "unresolved_items": []
        })
        for field in ("trips", "toll_reimbursements", "parking_reimbursements",
                      "passway_reimbursements", "documents", "unresolved_items"):
            bucket[field].extend(item.get(field, []))

    driver_results = []
    for key, bucket in sorted(by_driver.items()):
        driver_info = bucket["driver"] if isinstance(bucket["driver"], dict) else {"name": bucket["driver"]}
        for trip in bucket["trips"]:
            trip.setdefault("driver_name", driver_info.get("name"))
            trip.setdefault("driver_id", driver_info.get("id"))
        for category in ("toll_reimbursements", "parking_reimbursements", "passway_reimbursements"):
            for record in bucket[category]:
                record.setdefault("driver_name", driver_info.get("name"))
                record.setdefault("driver_id", driver_info.get("id"))
        trip_result = process_all_trips({"driver": bucket["driver"], "trips": bucket["trips"]})
        expense_result = process_reimbursements({
            "driver": bucket["driver"],
            "toll_reimbursements": bucket["toll_reimbursements"],
            "parking_reimbursements": bucket["parking_reimbursements"],
            "passway_reimbursements": bucket["passway_reimbursements"]
        })
        driver_results.append({
            "driver": bucket["driver"],
            "documents": bucket["documents"],
            "trips": trip_result["trips"],
            "toll_reimbursements": expense_result["toll_reimbursements"],
            "parking_reimbursements": expense_result["parking_reimbursements"],
            "passway_reimbursements": expense_result["passway_reimbursements"],
            "unresolved_items": bucket["unresolved_items"],
            "status": "pending_review"
        })

    review_required = bool(extraction_errors or grouping["unidentified_documents"] or grouping["review_items"])
    review_required = review_required or any(
        item.get("page_extraction_review_required") or item.get("document_type_review_required")
        for item in per_document_results
    )
    review_required = review_required or any(
        driver.get("unresolved_items")
        or any(trip.get("review_flags") for trip in driver.get("trips", []))
        or any(record.get("review_flags") for category in
               ("toll_reimbursements", "parking_reimbursements", "passway_reimbursements")
               for record in driver.get(category, []))
        for driver in driver_results
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
        "passway_reimbursements": [row for driver in driver_results for row in driver["passway_reimbursements"]],
        "unresolved_items": [item for driver in driver_results for item in driver["unresolved_items"]],
        "overall_review_required": True,
        "approval_status": "pending_review",
        "review_summary": {
            "extraction_errors": len(extraction_errors),
            "unidentified_documents": len(grouping["unidentified_documents"]),
            "grouping_conflicts": len(grouping["review_items"]),
            "review_required": True
        }
    }
