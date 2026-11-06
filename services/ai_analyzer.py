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


def assemble_batch_result(batch_id: str, per_document_results: list[dict],
                          extraction_errors: list[dict], documents_total: int) -> dict:
    return assemble_batch_result(batch_dir.name, per_document_results, extraction_errors, len(pdf_files))
