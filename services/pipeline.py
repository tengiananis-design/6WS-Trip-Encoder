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

    # Keep the per-driver view synchronized with the canonical top-level
    # records used by review and Google Sheets export.
    for group in result.get("driver_groups", []):
        group_name = group.get("driver", {}).get("name")
        group["trips"] = [
            trip for trip in result["trips"] if trip.get("driver_name") == group_name
        ]
        group["toll_reimbursements"] = [
            record for record in result["toll_reimbursements"] if record.get("driver_name") == group_name
        ]
        group["parking_reimbursements"] = [
            record for record in result["parking_reimbursements"] if record.get("driver_name") == group_name
        ]

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
            not group.get("driver", {}).get("name")
            or group.get("driver", {}).get("confidence") not in ("high", "verified")
            for group in result.get("driver_groups", [])
        )
    )
    result["approval_status"] = "pending_review"
    return result
