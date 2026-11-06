
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)


def normalize_amount(value: Any):
    """
    Convert an extracted amount into a decimal.
    Invalid or missing amounts remain None.
    """

    if value is None or value == "":
        return None

    try:
        cleaned = (
            str(value)
            .replace("₱", "")
            .replace(",", "")
            .replace("PHP", "")
            .strip()
        )

        return str(Decimal(cleaned))

    except (InvalidOperation, ValueError):
        return None


def normalize_expense(
    expense: dict,
    expense_type: str
) -> dict:
    """
    Standardize one reimbursement record.
    """

    amount = normalize_amount(
        expense.get("amount")
    )

    review_flags = list(
        expense.get("review_flags", [])
    )

    if amount is None:
        review_flags.append(
            "Missing or invalid reimbursement amount."
        )

    if not expense.get("date"):
        review_flags.append(
            "Reimbursement date is missing."
        )

    if not expense.get("location"):
        review_flags.append(
            "Reimbursement location is missing."
        )

    return {
        "expense_type": expense_type,
        "date": expense.get("date"),
        "location": expense.get("location"),
        "amount": amount,
        "receipt_reference": expense.get(
            "receipt_reference"
        ),
        "source_pages": expense.get(
            "source_pages", []
        ),
        "review_flags": list(dict.fromkeys(review_flags)),
        "status": (
            "review_required"
            if review_flags
            else "ready_for_review"
        )
    }


def process_reimbursements(
    analysis: dict
) -> dict:
    """
    Process toll and parking reimbursements separately.
    """

    toll_records = [
        normalize_expense(record, "TOLL")
        for record in analysis.get(
            "toll_reimbursements", []
        )
    ]

    parking_records = [
        normalize_expense(record, "PARKING")
        for record in analysis.get(
            "parking_reimbursements", []
        )
    ]

    all_records = toll_records + parking_records

    review_count = sum(
        1 for record in all_records
        if record["status"] == "review_required"
    )

    return {
        "driver": analysis.get("driver", {}),
        "toll_count": len(toll_records),
        "parking_count": len(parking_records),
        "total_reimbursement_records": len(all_records),
        "review_required_count": review_count,
        "toll_reimbursements": toll_records,
        "parking_reimbursements": parking_records,
        "approval_status": "pending_review"
    }
