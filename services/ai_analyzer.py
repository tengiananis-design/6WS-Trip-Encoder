
import os
import json
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from services.ocr_engine import extract_complete_pdf

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

_client = None


def get_ai_client():
    """Initialize Gemini using the environment API key."""

    global _client

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    if _client is None:
        _client = genai.Client(api_key=api_key)

    return _client


def classify_document(filename: str, text: str) -> str:
    """Make an initial document-type estimate."""

    combined = f"{filename} {text[:3000]}".lower()

    if "parking reimbursement" in combined:
        return "parking_reimbursement"

    if "toll reimbursement" in combined:
        return "toll_reimbursement"

    if any(term in combined for term in [
        "trip ticket",
        "trip summary",
        "line haul",
        "runsheet",
        "task information"
    ]):
        return "trip_ticket"

    return "unknown"


def build_analysis_prompt(documents: list[dict]) -> str:
    """
    Provide the extracted documents to Gemini.
    The AI must distinguish evidence from assumptions.
    """

    document_text = json.dumps(
        documents,
        ensure_ascii=False,
        indent=2
    )

    return f"""
You are the document analysis engine for 6WS Trip Encoder.

Your task is to analyze driver trip tickets and reimbursement
documents and prepare structured information for human review.

IMPORTANT RULES:

1. Do not invent missing dates, times, locations, trip numbers,
   amounts, driver names, or vehicle details.

2. Preserve the original source filename and PDF page number
   for every extracted fact.

3. Separate trip tickets, toll reimbursements, and parking
   reimbursements.

4. Reconstruct trips using actual chronological evidence,
   not merely the order of pages.

5. Identify SOC origins, intermediate hubs, destinations,
   returns, and possible backloads.

6. Apply the established encoding rules:
   - Intermediate hubs belong in DROP OFFS.
   - The farthest or last outbound hub before returning
     is the DESTINATION.
   - Return movements must be represented separately.
   - A non-SOC to non-SOC movement is a possible backload
     only when the destination hub contains standalone
     token MFM, case-insensitive.
   - SOC to MFM is an outbound movement, not a backload.

7. Keep toll and parking expenses separate from trip records.

8. If information conflicts or is unclear, flag it for review.
   Never silently choose an unsupported interpretation.

9. Do not approve or export anything.

10. Do not assume that multiple PDFs belong to the same driver
    unless the evidence supports that relationship.

Return valid JSON only, using this structure:

{{
  "driver": {{
    "name": null,
    "confidence": "unknown",
    "evidence": []
  }},
  "documents": [
    {{
      "filename": "",
      "document_type": "trip_ticket | toll_reimbursement | parking_reimbursement | unknown",
      "pages_used": [],
      "notes": []
    }}
  ],
  "trips": [
    {{
      "trip_ticket_number": null,
      "line_haul_trip_number": null,
      "trip_date": null,
      "origin": null,
      "drop_offs": [],
      "destination": null,
      "return_origin": null,
      "segments": [
        {{
          "sequence": 1,
          "origin": null,
          "destination": null,
          "departure_time": null,
          "arrival_time": null,
          "movement_type": "outbound | return | backload | parking | unknown",
          "source_pages": []
        }}
      ],
      "check_in_time_actual": null,
      "docking_time": null,
      "check_out_time_actual": null,
      "review_flags": [],
      "confidence": "high | medium | low"
    }}
  ],
  "toll_reimbursements": [
    {{
      "date": null,
      "location": null,
      "amount": null,
      "receipt_reference": null,
      "source_pages": [],
      "review_flags": []
    }}
  ],
  "parking_reimbursements": [
    {{
      "date": null,
      "location": null,
      "amount": null,
      "receipt_reference": null,
      "source_pages": [],
      "review_flags": []
    }}
  ],
  "unresolved_items": [],
  "overall_review_required": true
}}

SOURCE DOCUMENTS:

{document_text}
"""


def analyze_documents(documents: list[dict]) -> dict[str, Any]:
    """Send extracted PDF evidence to Gemini for analysis."""

    client = get_ai_client()

    prompt = build_analysis_prompt(documents)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
    )

    if not response.text:
        raise RuntimeError(
            "AI returned an empty analysis response."
        )

    try:
        result = json.loads(response.text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "AI response was not valid JSON."
        ) from error

    result["analysis_model"] = GEMINI_MODEL
    result["approval_status"] = "pending_review"

    return result


async def analyze_batch_documents(
    batch_dir: str | Path
) -> dict[str, Any]:
    """
    Extract all PDFs in a batch and analyze their contents.

    This is an initial implementation. It does not yet
    establish verified driver-package relationships across
    multiple uploads.
    """

    batch_dir = Path(batch_dir)

    pdf_files = sorted(
        batch_dir.glob("*.pdf")
    )

    if not pdf_files:
        raise ValueError(
            "No PDF documents found in this batch."
        )

    extracted_documents = []
    extraction_errors = []

    for pdf_path in pdf_files:
        try:
            extraction = extract_complete_pdf(pdf_path)

            document_text = "\n".join(
                f"PAGE {page['page_number']}:\n{page['text']}"
                for page in extraction["pages"]
            )

            extracted_documents.append({
                "filename": pdf_path.name,
                "document_type": classify_document(
                    pdf_path.name,
                    document_text
                ),
                "total_pages": extraction["total_pages"],
                "extraction_status": extraction["status"],
                "pages": extraction["pages"]
            })

        except Exception as error:
            logger.exception(
                "Failed to extract %s",
                pdf_path.name
            )

            extraction_errors.append({
                "filename": pdf_path.name,
                "error": str(error)
            })

    if not extracted_documents:
        raise RuntimeError(
            "All PDF extractions failed."
        )

    analysis = analyze_documents(
        extracted_documents
    )

    analysis["batch_id"] = batch_dir.name
    analysis["documents_processed"] = len(
        extracted_documents
    )
    analysis["extraction_errors"] = extraction_errors
    analysis["approval_status"] = "pending_review"

    return analysis
