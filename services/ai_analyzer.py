
import os
import json
import logging
import time
import asyncio
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from services.ocr_engine import extract_complete_pdf

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EXTRACTION_WORKERS = max(1, min(int(os.getenv("PDF_EXTRACTION_WORKERS", "2")), 4))
DRIVER_ANALYSIS_WORKERS = max(1, min(int(os.getenv("DRIVER_ANALYSIS_WORKERS", "3")), 4))
IDENTIFICATION_BATCH_SIZE = max(1, min(int(os.getenv("IDENTIFICATION_BATCH_SIZE", "10")), 20))
IDENTIFICATION_WORKERS = max(1, min(int(os.getenv("IDENTIFICATION_WORKERS", "3")), 4))
_client = None


def get_ai_client():
    global _client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client


def generate_json(prompt: str) -> dict:
    # Gemini can temporarily return 429/503 during demand spikes. Retry only
    # transient capacity/rate-limit errors; fail fast for invalid requests/keys.
    for attempt in range(4):
        try:
            response = get_ai_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            break
        except Exception as error:
            status = getattr(error, "status_code", None) or getattr(error, "code", None)
            message = str(error)
            transient = status in (429, 500, 502, 503, 504) or any(
                token in message for token in ("429", "500", "502", "503", "504")
            )
            if not transient or attempt == 3:
                raise
            delay = min(2 ** (attempt + 1), 12)
            logger.warning(
                "Gemini transient error (attempt %s/4); retrying in %s seconds: %s",
                attempt + 1, delay, message[:300]
            )
            time.sleep(delay)
    if not response.text:
        raise RuntimeError("AI returned an empty response.")
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as error:
        raise RuntimeError("AI response was not valid JSON.") from error


def identify_document_owners(documents: list[dict]) -> dict:
    # Only send a short evidence sample per file in this first pass. The full
    # extracted pages are sent later only to the matching driver's analysis.
    summaries = []
    for document in documents:
        pages = document.get("pages", [])
        snippets = [
            {"page": page.get("page_number"), "text": (page.get("text") or "")[:900]}
            for page in pages[:3]
        ]
        summaries.append({
            "document_id": document.get("stored_filename") or document["filename"],
            "original_filename": document["filename"],
            "page_count": document.get("total_pages"),
            "sample_pages": snippets
        })

    prompt = """
Identify the document type and driver named in each PDF. Use only visible evidence.
Do not infer that two similar names are the same person. If no clear driver name,
return null. Keep exact original filenames. Return JSON only:
{"documents":[{"document_id":"exact document_id","driver_name":null,
"document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | unknown",
"confidence":"high | medium | low","evidence_pages":[1],"review_flags":[]}]}
Documents:
""" + json.dumps(summaries, ensure_ascii=False, separators=(",", ":"))
    result = generate_json(prompt)
    by_filename = {}
    for item in result.get("documents", []):
        document_id = item.get("document_id")
        if document_id:
            by_filename[document_id] = item
    return by_filename


async def identify_documents_in_parallel(documents: list[dict]) -> tuple[dict, list[dict]]:
    """Classify manageable file groups concurrently; isolate failed groups for review."""
    chunks = [
        documents[index:index + IDENTIFICATION_BATCH_SIZE]
        for index in range(0, len(documents), IDENTIFICATION_BATCH_SIZE)
    ]
    semaphore = asyncio.Semaphore(IDENTIFICATION_WORKERS)

    async def classify(chunk):
        async with semaphore:
            try:
                return await asyncio.to_thread(identify_document_owners, chunk), None
            except Exception as error:
                logger.exception("Document identification chunk failed")
                return {}, {
                    "filenames": [item["filename"] for item in chunk],
                    "error": str(error)
                }

    results = await asyncio.gather(*(classify(chunk) for chunk in chunks))
    identified = {}
    errors = []
    for mapping, error in results:
        identified.update(mapping)
        if error:
            errors.append(error)
    return identified, errors


def build_driver_prompt(driver_name: str, documents: list[dict]) -> str:
    payload = []
    for document in documents:
        payload.append({
            "filename": document["filename"],
            "document_type": document.get("document_type", "unknown"),
            "driver_identification": document.get("driver_identification", {}),
            "pages": [
                {"page_number": page.get("page_number"), "text": page.get("text", "")}
                for page in document.get("pages", [])
            ]
        })

    return """
You are the trip-ticket and reimbursement analysis engine for 6WS Trip Encoder.
Analyze documents belonging to ONE driver only: """ + json.dumps(driver_name) + """.
Return valid JSON only. Never invent missing facts. Preserve source filename and
PDF page numbers for every fact. If a document appears to name another driver,
flag it and do not silently merge its records.

Trip reconstruction rules:
- Reconstruct movements chronologically using evidence, not PDF/page order alone.
- Intermediate hubs belong in DROP OFFS; the farthest/last outbound hub before
  the return is DESTINATION.
- Return movements are separate rows.
- A non-SOC to non-SOC movement is a possible backload only when its destination
  hub contains standalone token MFM, case-insensitive.
- SOC to MFM is outbound, not a backload.
- Do not combine separate trips merely because they share a date or driver.
- Keep toll and parking reimbursement records separate from trip rows.
- Flag conflicting dates, driver identities, missing route legs, unreadable pages,
  uncertain amounts, and ambiguous trip sequence.

Return JSON with this schema:
{
 "driver":{"name":""" + json.dumps(driver_name) + ""","confidence":"high | medium | low","evidence":[]},
 "documents":[{"filename":"","document_type":"trip_ticket | toll_reimbursement | parking_reimbursement | unknown","pages_used":[],"notes":[]}],
 "trips":[{"driver_name":""" + json.dumps(driver_name) + ""","trip_ticket_number":null,"line_haul_trip_number":null,"trip_date":null,"origin":null,"drop_offs":[],"destination":null,"return_origin":null,"segments":[{"sequence":1,"origin":null,"destination":null,"departure_time":null,"arrival_time":null,"movement_type":"outbound | return | backload | unknown","source_pages":[],"source_filename":""}],"check_in_time_actual":null,"docking_time":null,"check_out_time_actual":null,"review_flags":[],"confidence":"high | medium | low","source_filename":"","source_pages":[]}],
 "toll_reimbursements":[{"driver_name":""" + json.dumps(driver_name) + ""","date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"source_filename":"","review_flags":[]}],
 "parking_reimbursements":[{"driver_name":""" + json.dumps(driver_name) + ""","date":null,"location":null,"amount":null,"receipt_reference":null,"source_pages":[],"source_filename":"","review_flags":[]}],
 "unresolved_items":[],
 "overall_review_required":false
}
Source documents:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def analyze_driver(driver_name: str, documents: list[dict]) -> dict[str, Any]:
    return generate_json(build_driver_prompt(driver_name, documents))


def _extract_one(pdf_path: Path, original_name: str) -> dict:
    extraction = extract_complete_pdf(pdf_path)
    pages = [
        {"page_number": page["page_number"], "text": page.get("text", ""),
         "source": page.get("source"), "status": page.get("status")}
        for page in extraction["pages"]
    ]
    return {
        "filename": original_name,
        "stored_filename": pdf_path.name,
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
    extraction_errors = []
    with ThreadPoolExecutor(max_workers=min(EXTRACTION_WORKERS, len(pdf_records))) as pool:
        tasks = [loop.run_in_executor(pool, _extract_one, path, name) for path, name in pdf_records]
        extraction_results = await asyncio.gather(*tasks, return_exceptions=True)

    extracted_documents = []
    for (path, name), result in zip(pdf_records, extraction_results):
        if isinstance(result, Exception):
            logger.error("PDF extraction failed for %s: %s", name, result)
            extraction_errors.append({"filename": name, "error": str(result)})
        else:
            extracted_documents.append(result)

    if not extracted_documents:
        raise RuntimeError("All PDF extractions failed.")

    # First AI pass identifies each document's driver and type from short samples.
    # This prevents a single 100-PDF prompt and avoids mixing drivers.
    identification, identification_errors = await identify_documents_in_parallel(extracted_documents)
    if identification_errors and not identification:
        details = "; ".join(
            f"{', '.join(item.get('filenames', []))}: {item.get('error', 'identification failed')}"
            for item in identification_errors
        )
        raise RuntimeError(
            "Document identification failed for every file group; no trustworthy driver results were produced. "
            + details
        )
    grouped = {}
    unidentified = []
    for document in extracted_documents:
        identity = identification.get(document.get("stored_filename") or document["filename"], {})
        document["driver_identification"] = identity
        document["document_type"] = identity.get("document_type", "unknown")
        driver_name = identity.get("driver_name")
        if not driver_name or identity.get("confidence") == "low":
            unidentified.append({
                "filename": document["filename"],
                "reason": "Driver identity missing or low confidence.",
                "identification": identity
            })
            continue
        grouped.setdefault(driver_name.strip(), []).append(document)

    semaphore = asyncio.Semaphore(DRIVER_ANALYSIS_WORKERS)
    async def run_driver(driver_name, driver_docs):
        async with semaphore:
            try:
                analysis = await asyncio.to_thread(analyze_driver, driver_name, driver_docs)
                analysis["driver"] = analysis.get("driver") or {"name": driver_name}
                analysis["driver"]["name"] = driver_name
                analysis["document_count"] = len(driver_docs)
                analysis["source_documents"] = [doc["filename"] for doc in driver_docs]
                # Enforce the review gate from concrete flags, even if the model
                # forgets to set the batch-level boolean correctly.
                flagged_records = []
                for collection in ("trips", "toll_reimbursements", "parking_reimbursements"):
                    for record in analysis.get(collection, []):
                        if record.get("review_flags") or record.get("confidence") == "low":
                            flagged_records.append(record)
                failed_pages = [
                    f"{doc['filename']} page {page['page_number']}"
                    for doc in driver_docs for page in doc.get("pages", [])
                    if page.get("status") == "failed" or not (page.get("text") or "").strip()
                ]
                identity_review = [
                    doc["filename"] for doc in driver_docs
                    if doc.get("driver_identification", {}).get("confidence") != "high"
                    or doc.get("document_type") == "unknown"
                ]
                analysis["overall_review_required"] = bool(
                    analysis.get("unresolved_items") or flagged_records or failed_pages
                    or identity_review or analysis.get("overall_review_required", False)
                )
                if identity_review:
                    analysis.setdefault("unresolved_items", []).extend(
                        f"Driver or document-type identification needs verification: {name}"
                        for name in identity_review
                    )
                if failed_pages:
                    analysis.setdefault("unresolved_items", []).extend(
                        f"OCR/text extraction empty or failed: {item}" for item in failed_pages
                    )
                return analysis
            except Exception as error:
                logger.exception("Driver analysis failed for %s", driver_name)
                return {
                    "driver": {"name": driver_name},
                    "document_count": len(driver_docs),
                    "source_documents": [doc["filename"] for doc in driver_docs],
                    "trips": [],
                    "toll_reimbursements": [],
                    "parking_reimbursements": [],
                    "unresolved_items": [f"Driver analysis failed: {error}"],
                    "overall_review_required": True,
                    "analysis_error": str(error)
                }

    driver_names = sorted(grouped)
    driver_results = await asyncio.gather(*[
        run_driver(name, grouped[name]) for name in driver_names
    ])

    # Keep a combined top-level view for the current dashboard/export interface,
    # while retaining each independently analyzed driver package as the source of truth.
    combined = {
        "batch_id": batch_dir.name,
        "driver": {"name": driver_names[0] if len(driver_names) == 1 else ("MULTIPLE DRIVERS" if driver_names else None)},
        "driver_groups": driver_results,
        "driver_count": len(driver_results),
        "documents": [
            {"filename": doc["filename"], "document_type": doc.get("document_type"),
             "pages_used": [page["page_number"] for page in doc.get("pages", [])],
             "extraction_status": doc.get("extraction_status")}
            for doc in extracted_documents
        ],
        "trips": [trip for group in driver_results for trip in group.get("trips", [])],
        "toll_reimbursements": [item for group in driver_results for item in group.get("toll_reimbursements", [])],
        "parking_reimbursements": [item for group in driver_results for item in group.get("parking_reimbursements", [])],
        "unresolved_items": [item for group in driver_results for item in group.get("unresolved_items", [])],
        "unidentified_documents": unidentified,
        "extraction_errors": extraction_errors,
        "identification_errors": identification_errors,
        "documents_processed": len(extracted_documents),
        "documents_received": len(pdf_records),
        "analysis_model": GEMINI_MODEL,
        "approval_status": "pending_review",
        "overall_review_required": bool(unidentified or extraction_errors or identification_errors or any(
            group.get("overall_review_required", True) for group in driver_results
        ))
    }
    return combined
