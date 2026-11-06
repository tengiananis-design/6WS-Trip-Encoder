import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)


def normalize_amount(value: Any):
    if value is None or value == "":
        return None
    try:
        cleaned = str(value).replace("₱", "").replace(",", "").replace("PHP", "").strip()
        return str(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


def _clean_identifier(value: Any):
    if value is None or value == "":
        return None
    return str(value).strip()


def normalize_expense(expense: dict, expense_type: str) -> dict:
    amount = normalize_amount(expense.get("amount"))
    flags = list(expense.get("review_flags", []) or [])
    date_field = "date" if expense_type == "TOLL" else "trip_date"
    date_value = expense.get(date_field) or expense.get("date")
    if not date_value:
        flags.append("Expense/trip date is missing.")
    if amount is None:
        flags.append("Missing or invalid reimbursement amount.")

    common = {
        "expense_type": expense_type,
        "trip_date": date_value,
        "line_haul_trip_number": _clean_identifier(expense.get("line_haul_trip_number")),
        "driver_id": _clean_identifier(expense.get("driver_id")),
        "driver_name": expense.get("driver_name"),
        "plate_no": _clean_identifier(expense.get("plate_no")),
        "fleet_type": expense.get("fleet_type"),
        "origin": expense.get("origin"),
        "destination": expense.get("destination"),
        "date_of_or": expense.get("date_of_or"),
        "tag_number": _clean_identifier(expense.get("tag_number")),
        "amount": amount,
        "source_pages": expense.get("source_pages", []) or [],
        "review_flags": [],
    }

    if expense_type == "TOLL":
        common.update({
            "date": date_value,
            "rfid_toll_charges": expense.get("rfid_toll_charges"),
            "entry_point": expense.get("entry_point"),
            "exit_point": expense.get("exit_point"),
        })
        if not expense.get("entry_point") or not expense.get("exit_point"):
            flags.append("Toll entry or exit point is missing.")
        if not expense.get("rfid_toll_charges"):
            flags.append("RFID toll provider/type is missing.")
    else:
        common.update({
            "vendor_name": expense.get("vendor_name"),
            "address": expense.get("address"),
        })
        if not expense.get("vendor_name"):
            flags.append("Vendor name is missing.")
        if not expense.get("address"):
            flags.append("Vendor/address detail is missing.")

    if not common["driver_name"]:
        flags.append("Driver name is missing from the expense record.")
    if not common["line_haul_trip_number"]:
        flags.append("Linehaul trip number is missing from the expense record.")

    common["review_flags"] = list(dict.fromkeys(flags))
    common["status"] = "review_required" if common["review_flags"] else "ready_for_review"
    return common


def process_reimbursements(analysis: dict) -> dict:
    toll = [normalize_expense(x, "TOLL") for x in (analysis.get("toll_reimbursements", []) or [])]
    parking = [normalize_expense(x, "PARKING") for x in (analysis.get("parking_reimbursements", []) or [])]
    passway = [normalize_expense(x, "PASSWAY") for x in (analysis.get("passway_reimbursements", []) or [])]
    all_records = toll + parking + passway
    return {
        "driver": analysis.get("driver", {}),
        "toll_count": len(toll),
        "parking_count": len(parking),
        "passway_count": len(passway),
        "total_reimbursement_records": len(all_records),
        "review_required_count": sum(1 for x in all_records if x["status"] == "review_required"),
        "toll_reimbursements": toll,
        "parking_reimbursements": parking,
        "passway_reimbursements": passway,
        "approval_status": "pending_review"
    }
