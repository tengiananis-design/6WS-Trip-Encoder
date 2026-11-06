
import re
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


def normalize_driver_name(name: Any) -> str:
    """
    Normalize driver names for comparison.
    Does not assume that similar names are the same person.
    """

    if not name:
        return ""

    name = str(name).strip().upper()

    name = re.sub(
        r"[^A-Z0-9\s]",
        " ",
        name
    )

    return re.sub(r"\s+", " ", name).strip()


def get_document_driver(document: dict) -> str:
    """
    Retrieve the driver name from verified document data.
    """

    driver = document.get("driver", {})

    if isinstance(driver, dict):
        name = driver.get("name")
    else:
        name = driver

    if not name:
        name = document.get("driver_name")

    return normalize_driver_name(name)


def group_documents_by_driver(
    documents: list[dict],
    manual_assignments: dict | None = None
) -> dict:
    """
    Group documents by driver.

    Documents are grouped only when their driver names
    match after normalization.

    Unidentified or conflicting documents are placed
    in a separate review queue.

    manual_assignments example:
    {
        "trip-ticket-001.pdf": "AMADO B CANAR",
        "toll-001.pdf": "AMADO B CANAR"
    }
    """

    manual_assignments = manual_assignments or {}

    grouped = defaultdict(list)
    unidentified = []
    review_items = []

    for document in documents:
        filename = document.get("filename", "unknown.pdf")

        extracted_name = get_document_driver(document)

        assigned_name = normalize_driver_name(
            manual_assignments.get(filename)
        )

        if assigned_name:
            if extracted_name and extracted_name != assigned_name:
                review_items.append({
                    "filename": filename,
                    "reason": "Manual assignment conflicts with extracted driver name.",
                    "extracted_driver": extracted_name,
                    "assigned_driver": assigned_name
                })

                unidentified.append(document)
                continue

            driver_name = assigned_name

        else:
            driver_name = extracted_name

        if not driver_name:
            unidentified.append(document)

            review_items.append({
                "filename": filename,
                "reason": "Driver identity could not be established."
            })

            continue

        grouped[driver_name].append(document)

    driver_groups = []

    for driver_name, driver_documents in sorted(grouped.items()):
        document_types = [
            document.get("document_type", "unknown")
            for document in driver_documents
        ]

        driver_groups.append({
            "driver_name": driver_name,
            "document_count": len(driver_documents),
            "documents": driver_documents,
            "document_types": document_types,
            "contains_trip_ticket": (
                "trip_ticket" in document_types
            ),
            "contains_toll_reimbursement": (
                "toll_reimbursement" in document_types
            ),
            "contains_parking_reimbursement": (
                "parking_reimbursement" in document_types
            ),
            "status": "pending_review"
        })

    return {
        "driver_count": len(driver_groups),
        "grouped_documents": driver_groups,
        "unidentified_documents": unidentified,
        "review_items": review_items,
        "approval_status": "pending_review"
    }


def assign_document_to_driver(
    filename: str,
    driver_name: str,
    documents: list[dict]
) -> dict:
    """
    Apply a manual driver assignment to a document.
    The assignment is returned for review and is not
    silently treated as verified identity.
    """

    normalized_name = normalize_driver_name(driver_name)

    if not normalized_name:
        raise ValueError("A valid driver name is required.")

    updated = False
    results = []

    for document in documents:
        item = document.copy()

        if item.get("filename") == filename:
            item["driver_name"] = normalized_name
            item["driver_assignment_source"] = "manual"
            item["driver_assignment_status"] = "pending_review"
            updated = True

        results.append(item)

    if not updated:
        raise ValueError(
            f"Document not found: {filename}"
        )

    return {
        "documents": results,
        "assigned_filename": filename,
        "assigned_driver": normalized_name,
        "status": "pending_review"
    }
