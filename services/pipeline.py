from services.trip_processor import process_all_trips
from services.reimbursement_processor import process_reimbursements


def validate_analysis_result(result: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("AI analysis result must be a JSON object.")

    result.setdefault("driver", {"name": None, "confidence": "unknown", "evidence": []})
    result.setdefault("documents", [])
    result.setdefault("trips", [])
    result.setdefault("toll_reimbursements", [])
    result.setdefault("parking_reimbursements", [])
    result.setdefault("unresolved_items", [])

    processed_trips = process_all_trips(result)
    processed_expenses = process_reimbursements(result)

    result["trips"] = processed_trips["trips"]
    result["toll_reimbursements"] = processed_expenses["toll_reimbursements"]
    result["parking_reimbursements"] = processed_expenses["parking_reimbursements"]

    flags = list(result.get("unresolved_items") or [])
    for trip in result["trips"]:
        flags.extend(trip.get("review_flags", []))
    for collection in ("toll_reimbursements", "parking_reimbursements"):
        for expense in result[collection]:
            flags.extend(expense.get("review_flags", []))

    result["unresolved_items"] = list(dict.fromkeys(str(flag) for flag in flags))
    result["overall_review_required"] = bool(
        result["unresolved_items"]
        or result.get("extraction_errors")
        or (
            not result.get("driver_groups")
            and (
                not result["driver"].get("name")
                or result["driver"].get("confidence") not in ("high", "verified")
            )
        )
        or any(
            group.get("driver", {}).get("confidence") not in ("high", "verified")
            for group in result.get("driver_groups", [])
        )
    )
    result["approval_status"] = "pending_review"
    return result
