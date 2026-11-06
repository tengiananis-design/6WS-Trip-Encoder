import os
import json
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from services.ocr_engine import extract_complete_pdf
from services.driver_grouping import group_documents_by_driver

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_CHARS_PER_PAGE = 18000
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
    if any(term in combined for term in ("trip ticket", "trip summary", "line haul", "runsheet", "task information")):
        return "trip_ticket"
    return "unknown"


def build_analysis_prompt(documents: list[dict]) -> str:
    document_text = json.dumps(documents, ensure_ascii=False)
    return f"""
You are the 6WS Trip Encoder evidence analyst. Analyze ONLY the supplied PDF evidence.
Never invent or silently infer dates, times, routes, trip numbers, drivers, amounts, or vehicle details.
Keep source filename and page numbers for each fact. Separate trip tickets, toll reimbursements and parking reimbursements.
Reconstruct trips chronologically from timestamps, not page order.
Encoding rules:
- Intermediate hubs go in DROP OFFS; farthest/last outbound hub before return is DESTINATION.
- Return movement is a separate segment back to the originating SOC.
- Non-SOC to non-SOC is a backload only if destination contains standalone token MFM (case-insensitive).
- SOC to MFM is outbound, never backload.
- Flag conflicts, missing values, uncertain grouping, and unreadable evidence.
- Do not approve or export.

Return valid JSON only:
{{
 "driver":{{"name":null,"confidence":"unknown","evidence":[]}},
 "documents":[{{"filename":"","document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | unknown","pages_used":[],"notes":[]}}],
 "trips":[{{"trip_ticket_number":null,"line_haul_trip_number":null,"trip_date":null,"origin":null,"drop_offs":[],"destination":null,"return_origin":null,"segments":[{{"sequence":1,"origin":null,"destination":null,"departure_time":null,"arrival_time":null,"movement_type":"outbound | return | backload | parking | unknown","source_pages":[]}}],"check_in_time_actual":null,"docking_time":null,"check_out_time_actual":null,"review_flags":[],"confidence":"high | medium | low"}}],
 "toll_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"review_flags":[]}}],
 "parking_reimbursements":[{{"date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"review_flags":[]}}],
 "unresolved_items":[],"overall_review_required":true
}}
EVIDENCE:
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
    if not isinstance(result, dict):
        raise RuntimeError("AI response must be a JSON object.")
    result["analysis_model"] = GEMINI_MODEL
    result["approval_status"] = "pending_review"
    return result


def _limit_page_text(pages: list[dict]) -> list[dict]:
    limited = []
    for page in pages:
        item = dict(page)
        text = str(item.get("text") or "")
        if len(text) > MAX_CHARS_PER_PAGE:
            item["text"] = text[:MAX_CHARS_PER_PAGE]
            item["truncated"] = True
        limited.append(item)
    return limited


async def analyze_batch_documents(batch_dir: str | Path, progress_callback=None) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    pdf_files = sorted(path for path in batch_dir.glob("*.pdf") if path.is_file())
    if not pdf_files:
        raise ValueError("No PDF documents found in this batch.")

    extracted_documents = []
    extraction_errors = []
    per_document_analyses = []

    # Analyze each PDF independently first. This prevents documents belonging
    # to different drivers from being merged just because they were uploaded
    # in the same batch. Requests are deliberately sequential to limit API bursts.
    total_files = len(pdf_files)
    for file_index, pdf_path in enumerate(pdf_files, start=1):
        try:
            if progress_callback:
                progress_callback(file_index - 1, total_files, pdf_path.name)
            extraction = extract_complete_pdf(pdf_path)
            pages = _limit_page_text(extraction.get("pages", []))
            text = "\\n".join(str(page.get("text") or "") for page in pages)
            document = {
                "filename": pdf_path.name,
                "document_type": classify_document(pdf_path.name, text),
                "driver_name": None,
                "total_pages": extraction.get("total_pages", len(pages)),
                "extraction_status": extraction.get("status", "unknown"),
                "pages": pages
            }
            extracted_documents.append(document)
            analysis = analyze_documents([document])
            driver = analysis.get("driver") or {}
            document["driver"] = driver
            document["driver_name"] = driver.get("name")
            per_document_analyses.append({"document": document, "analysis": analysis})
            if progress_callback:
                progress_callback(file_index, total_files, pdf_path.name)
        except Exception as error:
            logger.exception("Failed to extract/analyze %s", pdf_path.name)
            extraction_errors.append({"filename": pdf_path.name, "error": str(error)})
            if progress_callback:
                progress_callback(file_index, total_files, pdf_path.name)

    if not per_document_analyses:
        raise RuntimeError("No PDF documents could be analyzed successfully.")

    from services.driver_grouping import group_documents_by_driver
    grouping = group_documents_by_driver(extracted_documents)
    analyses_by_filename = {item["document"]["filename"]: item["analysis"] for item in per_document_analyses}

    groups = []
    all_trips, all_tolls, all_parking = [], [], []
    unresolved = []
    for group in grouping["grouped_documents"]:
        driver_name = group["driver_name"]
        group_docs = group["documents"]
        group_trips, group_tolls, group_parking = [], [], []
        confidences = []
        evidence = []
        for document in group_docs:
            analysis = analyses_by_filename[document["filename"]]
            driver = analysis.get("driver") or {}
            confidences.append(driver.get("confidence", "unknown"))
            evidence.extend(driver.get("evidence") or [])
            group_trips.extend(analysis.get("trips") or [])
            group_tolls.extend(analysis.get("toll_reimbursements") or [])
            group_parking.extend(analysis.get("parking_reimbursements") or [])
            unresolved.extend(analysis.get("unresolved_items") or [])

        confidence = "high" if confidences and all(value in ("high", "verified") for value in confidences) else "medium"
        for trip in group_trips:
            trip["driver_name"] = driver_name
        for expense in group_tolls + group_parking:
            expense["driver_name"] = driver_name

        group_result = {
            "driver": {"name": driver_name, "confidence": confidence, "evidence": evidence},
            "documents": [{"filename": d["filename"], "document_type": d.get("document_type"), "total_pages": d.get("total_pages")} for d in group_docs],
            "trips": group_trips,
            "toll_reimbursements": group_tolls,
            "parking_reimbursements": group_parking,
            "status": "pending_review"
        }
        groups.append(group_result)
        all_trips.extend(group_trips)
        all_tolls.extend(group_tolls)
        all_parking.extend(group_parking)

    unidentified_names = {d["filename"] for d in grouping["unidentified_documents"]}
    for filename in sorted(unidentified_names):
        unresolved.append({
            "type": "unidentified_driver_document",
            "filename": filename,
            "message": "Driver identity is missing or conflicting. Assign this PDF to a driver during review."
        })
    unresolved.extend(grouping["review_items"])

    unique_driver_names = {g["driver"]["name"] for g in groups}
    return {
        "driver": {
            "name": groups[0]["driver"]["name"] if len(groups) == 1 else None,
            "confidence": groups[0]["driver"]["confidence"] if len(groups) == 1 else "unknown",
            "evidence": [e for g in groups for e in g["driver"]["evidence"]]
        },
        "driver_groups": groups,
        "documents": [{"filename": d["filename"], "document_type": d.get("document_type"), "driver_name": d.get("driver_name"), "total_pages": d.get("total_pages"), "extraction_status": d.get("extraction_status")} for d in extracted_documents],
        "documents_processed": len(extracted_documents),
        "trips": all_trips,
        "toll_reimbursements": all_tolls,
        "parking_reimbursements": all_parking,
        "unresolved_items": unresolved,
        "extraction_errors": extraction_errors,
        "analysis_model": GEMINI_MODEL,
        "analysis_requests": len(per_document_analyses),
        "overall_review_required": bool(unresolved or extraction_errors or any(g["driver"]["confidence"] not in ("high", "verified") for g in groups)),
        "approval_status": "pending_review"
    }
