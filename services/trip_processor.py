import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def normalize_location(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def contains_mfm_token(location: str) -> bool:
    return bool(re.search(r"\bMFM\b", location or "", flags=re.IGNORECASE))


def is_soc_location(location: str) -> bool:
    return bool(re.search(r"\bSOC\s*\d+\b", location or "", flags=re.IGNORECASE))


def _same_soc(origin: str, destination: str) -> bool:
    pattern = re.compile(r"\bSOC\s*(\d+)\b", re.IGNORECASE)
    left, right = pattern.search(origin or ""), pattern.search(destination or "")
    return bool(left and right and left.group(1) == right.group(1)
                and normalize_location(origin).upper() == normalize_location(destination).upper())


def determine_movement_type(origin: str, destination: str) -> str:
    origin = normalize_location(origin)
    destination = normalize_location(destination)
    if not origin or not destination:
        return "unknown"
    if _same_soc(origin, destination):
        return "parking"
    if is_soc_location(origin):
        return "outbound"
    if not is_soc_location(origin) and not is_soc_location(destination) and contains_mfm_token(destination):
        return "backload"
    if is_soc_location(destination):
        return "return"
    return "intermediate"


def _sequence_key(segment: dict):
    # Only sort when the value is an ISO-like date/time or a recognizable time.
    value = segment.get("departure_time") or segment.get("arrival_time")
    if not value:
        return None
    value = str(value).strip()
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?$", value, re.IGNORECASE)
    if match:
        hour, minute, second, meridiem = match.groups()
        hour, minute, second = int(hour), int(minute), int(second or 0)
        if meridiem:
            hour %= 12
            if meridiem.upper() == "PM":
                hour += 12
        return (0, hour, minute, second)
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2}(?::\d{2})?)", value)
    if iso:
        return (1, iso.group(1), iso.group(2))
    return None


def normalize_segment(segment: dict, sequence: int) -> dict:
    origin = normalize_location(segment.get("origin"))
    destination = normalize_location(segment.get("destination"))
    # Reapply the agreed route rules instead of blindly trusting a model label.
    movement_type = determine_movement_type(origin, destination)
    if not origin or not destination:
        movement_type = "unknown"
    return {
        "sequence": sequence,
        "source_sequence": segment.get("sequence"),
        "origin": origin or None,
        "destination": destination or None,
        "departure_time": segment.get("departure_time"),
        "arrival_time": segment.get("arrival_time"),
        "movement_type": movement_type,
        "source_pages": segment.get("source_pages", []),
        "review_required": movement_type == "unknown"
    }


def process_trip(trip: dict) -> dict:
    raw_segments = trip.get("segments", []) or []
    keyed = [(_sequence_key(s), i, s) for i, s in enumerate(raw_segments)]
    if keyed and all(item[0] is not None for item in keyed):
        keyed.sort(key=lambda item: (item[0], item[1]))
    ordered_segments = [item[2] for item in keyed]
    segments = [normalize_segment(s, i) for i, s in enumerate(ordered_segments, start=1)]

    review_flags = list(trip.get("review_flags", []) or [])
    if not segments:
        review_flags.append("No movement segments were identified.")
    for segment in segments:
        if segment["review_required"]:
            review_flags.append(f"Segment {segment['sequence']} needs origin/destination review.")

    drop_offs = [normalize_location(x) for x in (trip.get("drop_offs") or []) if normalize_location(x)]
    if not trip.get("origin") or not trip.get("destination"):
        review_flags.append("Trip origin or final destination is missing.")
    if any(s["movement_type"] == "backload" for s in segments) and trip.get("with_backload") in (None, ""):
        review_flags.append("Backload movement detected but WITH BACKLOAD field was not confirmed.")

    return {
        "trip_ticket_number": trip.get("trip_ticket_number"),
        "line_haul_trip_number": trip.get("line_haul_trip_number"),
        "trip_date": trip.get("trip_date"),
        "driver_id": trip.get("driver_id"),
        "driver_name": trip.get("driver_name"),
        "plate_no": trip.get("plate_no"),
        "fleet_type": trip.get("fleet_type"),
        "valid_time_circled": trip.get("valid_time_circled"),
        "docking_date_or_check_out_date": trip.get("docking_date_or_check_out_date"),
        "origin": normalize_location(trip.get("origin")) or None,
        "drop_offs": drop_offs,
        "destination": normalize_location(trip.get("destination")) or None,
        "with_backload": trip.get("with_backload"),
        "demurrage_availment": trip.get("demurrage_availment"),
        "remarks": trip.get("remarks"),
        "late": trip.get("late"),
        "return_origin": normalize_location(trip.get("return_origin")) or None,
        "segments": segments,
        "check_in_time_actual": trip.get("check_in_time_actual"),
        "docking_time": trip.get("docking_time"),
        "check_out_time": trip.get("check_out_time") or trip.get("check_out_time_actual"),
        "review_flags": list(dict.fromkeys(review_flags)),
        "confidence": trip.get("confidence", "low"),
        "status": "review_required" if review_flags else "ready_for_review"
    }


def process_all_trips(analysis: dict) -> dict:
    processed = [process_trip(trip) for trip in (analysis.get("trips", []) or [])]
    review_count = sum(1 for trip in processed if trip["status"] == "review_required")
    return {
        "driver": analysis.get("driver", {}),
        "batch_id": analysis.get("batch_id"),
        "trip_count": len(processed),
        "review_required_count": review_count,
        "trips": processed,
        "approval_status": "pending_review"
    }
