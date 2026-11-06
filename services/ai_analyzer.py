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


MAX_PAGES_PER_AI_REQUEST = 12
PAGE_OVERLAP = 2


def _page_windows(pages: list[dict], size: int = MAX_PAGES_PER_AI_REQUEST,
                  overlap: int = PAGE_OVERLAP) -> list[list[dict]]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("Invalid AI page-window configuration.")
    windows = []
    start = 0
    while start < len(pages):
        end = min(start + size, len(pages))
        windows.append(pages[start:end])
        if end == len(pages):
            break
        start = end - overlap
    return windows


def _page_numbers(record: dict) -> set:
    values = record.get("source_pages") or record.get("pages_used") or []
    return {str(value) for value in values if value is not None}


def _merge_record(target: dict, incoming: dict, label: str) -> dict:
    merged = dict(target)
    conflicts = list(merged.get("review_flags") or [])
    for key, value in incoming.items():
        if key == "_analysis_window":
            continue
        if key in ("source_pages", "pages_used"):
            merged[key] = sorted(set(merged.get(key) or []) | set(value or []),
                                 key=lambda item: (str(type(item)), str(item)))
        elif key in ("segments",):
            existing = merged.setdefault(key, [])
            known = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in existing}
            for segment in value or []:
                signature = json.dumps(segment, sort_keys=True, ensure_ascii=False)
                if signature not in known:
                    existing.append(segment)
                    known.add(signature)
        elif key in ("drop_offs",):
            existing = merged.setdefault(key, [])
            for item in value or []:
                if item not in existing:
                    existing.append(item)
        elif merged.get(key) in (None, "", []) and value not in (None, "", []):
            merged[key] = value
        elif value not in (None, "", []) and merged.get(key) not in (None, "", []) and merged.get(key) != value:
            conflicts.append(f"Conflicting {label} field across page windows: {key}.")
    if conflicts:
        merged["review_flags"] = list(dict.fromkeys(conflicts))
    return merged


def _merge_chunk_records(records: list[dict], kind: str) -> list[dict]:
    merged = []
    by_trip_identity = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if kind == "trips":
            trip_number = record.get("line_haul_trip_number") or record.get("trip_ticket_number")
            if trip_number:
                identity = ("trip", str(trip_number).strip().casefold(),
                            str(record.get("trip_date") or "").strip())
            else:
                identity = ("exact", json.dumps(record, sort_keys=True, ensure_ascii=False))
            candidates = by_trip_identity.get(identity, [])
            match = next((position for position in candidates
                          if merged[position].get("_analysis_window") != record.get("_analysis_window")), None)
            if match is not None:
                merged[match] = _merge_record(merged[match], record, "trip")
            else:
                position = len(merged)
                by_trip_identity.setdefault(identity, []).append(position)
                merged.append(dict(record))
            continue

        pages = _page_numbers(record)
        stable_fields = ("date", "trip_date", "date_of_or", "tag_number", "amount",
                         "origin", "destination", "entry_point", "exit_point",
                         "vendor_name", "address", "rfid_toll_charges")
        signature = tuple(str(record.get(field) or "").strip().casefold() for field in stable_fields)
        duplicate_index = None
        for index, existing in enumerate(merged):
            if (existing.get("_analysis_window") != record.get("_analysis_window")
                    and signature == tuple(str(existing.get(field) or "").strip().casefold() for field in stable_fields)):
                if pages and pages.intersection(_page_numbers(existing)):
                    duplicate_index = index
                    break
        if duplicate_index is None:
            merged.append(dict(record))
        else:
            merged[duplicate_index] = _merge_record(merged[duplicate_index], record, kind)
    return merged


def merge_page_window_analyses(analyses: list[dict], filename: str,
                               total_pages: int) -> dict:
    if not analyses:
        raise RuntimeError(f"No AI page windows were successfully analyzed for {filename}.")
    drivers = [item.get("driver") for item in analyses if item.get("driver")]
    driver = drivers[0] if drivers else {}
    driver_names = {
        normalize_driver_name(item.get("name"))
        for item in drivers if isinstance(item, dict) and item.get("name")
    }
    conflicts = len(driver_names) > 1
    merged = {
        "driver": driver,
        "documents": [],
        "trips": [],
        "toll_reimbursements": [],
        "parking_reimbursements": [],
        "passway_reimbursements": [],
        "unresolved_items": []
    }
    for window_index, item in enumerate(analyses):
        for document in item.get("documents", []) or []:
            merged["documents"].append(document)
        for field in ("trips", "toll_reimbursements", "parking_reimbursements",
                      "passway_reimbursements"):
            for record in item.get(field, []) or []:
                marked = dict(record)
                marked["_analysis_window"] = window_index
                merged[field].append(marked)
        merged["unresolved_items"].extend(item.get("unresolved_items", []) or [])
    merged["trips"] = _merge_chunk_records(merged["trips"], "trips")
    for field in ("toll_reimbursements", "parking_reimbursements", "passway_reimbursements"):
        merged[field] = _merge_chunk_records(merged[field], field)
    for field in ("trips", "toll_reimbursements", "parking_reimbursements", "passway_reimbursements"):
        for record in merged[field]:
            record.pop("_analysis_window", None)
    unique_documents = {}
    for document in merged["documents"]:
        key = document.get("filename") or filename
        if key not in unique_documents:
            unique_documents[key] = dict(document)
        else:
            unique_documents[key] = _merge_record(unique_documents[key], document, "document")
    merged["documents"] = list(unique_documents.values()) or [{
        "filename": filename, "document_type": "unknown",
        "pages_used": list(range(1, total_pages + 1)), "notes": []
    }]
    merged["chunk_count"] = len(analyses)
    merged["chunked_analysis_review_required"] = len(analyses) > 1 or conflicts
    if len(analyses) > 1:
        note = "Long PDF analyzed in overlapping page windows; verify trip and receipt records across page boundaries."
        for document in merged["documents"]:
            document.setdefault("notes", []).append(note)
        for field in ("trips", "toll_reimbursements", "parking_reimbursements", "passway_reimbursements"):
            for record in merged[field]:
                flags = record.setdefault("review_flags", [])
                flags.append("Long PDF used multiple AI page windows; verify this record against the source pages.")
    if conflicts:
        merged.setdefault("unresolved_items", []).append({
            "filename": filename,
            "reason": "Conflicting driver identities were returned by different page windows.",
            "driver_candidates": sorted(driver_names)
        })
    return merged


async def analyze_documents_with_retry(documents: list[dict], attempts: int = 3) -> dict:
    last_error = None
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(analyze_documents, documents)
        except Exception as error:
            last_error = error
            if attempt + 1 >= attempts:
                break
            delay = min(2 ** (attempt + 1), 8)
            logger.warning("AI request failed (attempt %s/%s); retrying in %ss: %s",
                           attempt + 1, attempts, delay, error)
            await asyncio.sleep(delay)
    raise RuntimeError(f"AI analysis failed after {attempts} attempts: {last_error}") from last_error


def _driver_name(result: dict) -> str:
    driver = result.get("driver") or {}
    return driver.get("name") if isinstance(driver, dict) else str(driver or "")


def assemble_batch_result(batch_id: str, per_document_results: list[dict],
                          extraction_errors: list[dict], documents_total: int) -> dict:
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
        "batch_id": batch_id,
        "analysis_model": GEMINI_MODEL,
        "documents_processed": len(per_document_results),
        "documents_total": documents_total,
        "extraction_errors": extraction_errors,
        "driver_count": len(driver_results),
        "drivers": driver_results,
        "grouping": {
            "driver_count": grouping["driver_count"],
            "grouped_documents": grouping["grouped_documents"],
            "unidentified_documents": grouping["unidentified_documents"],
            "review_items": grouping["review_items"]
        },
        "document_analyses": per_document_results,
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
            windows = _page_windows(pages)
            window_analyses = []
            for window_index, page_window in enumerate(windows, start=1):
                prompt_doc = [{
                    "filename": pdf_path.name,
                    "document_type_hint": hint,
                    "total_pages": extraction.get("total_pages"),
                    "page_window": window_index,
                    "page_window_count": len(windows),
                    "pages": page_window
                }]
                window_analyses.append(await analyze_documents_with_retry(prompt_doc))
            analysis = merge_page_window_analyses(
                window_analyses, pdf_path.name, len(pages)
            )
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
            analysis["original_driver"] = analysis.get("driver") or {}
            analysis["driver_assignment_source"] = "extracted"
            for section in ("trips", "toll_reimbursements", "parking_reimbursements", "passway_reimbursements"):
                for record in analysis.get(section, []):
                    record.setdefault("source_filename", pdf_path.name)
            analysis["extraction_status"] = extraction.get("status")
            analysis["page_extraction_review_required"] = any(p.get("status") == "failed" for p in pages)
            analysis["document_type_review_required"] = any(d.get("document_type") == "unknown" for d in docs)
            per_document_results.append(analysis)
        except Exception as error:
            logger.exception("Failed to process %s", pdf_path.name)
            extraction_errors.append({"filename": pdf_path.name, "error": str(error)})

    if not per_document_results:
        raise RuntimeError("No PDF documents could be analyzed successfully.")

    return assemble_batch_result(batch_dir.name, per_document_results, extraction_errors, len(pdf_files))
