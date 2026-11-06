
import logging
import re
from datetime import datetime, timezone
from dateutil import parser as date_parser
from typing import Any

logger = logging.getLogger(__name__)


def normalize_location(value: Any) -> str:
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def contains_mfm_token(location: str) -> bool:
    """
    MFM must be a standalone token.
    Examples:
    MFM Pulilan = True
    MFM Marikina = True
    AMFM Hub = False
    MFM2 = False
    """

    return bool(
        re.search(
            r"\bMFM\b",
            location,
            flags=re.IGNORECASE
        )
    )


def soc_identifier(location: str) -> str | None:
    match = re.search(r"\b(SOC\s*\d+)\b", location, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1)).upper() if match else None


def is_soc_location(location: str) -> bool:
    """Identify locations containing an SOC identifier."""
    return soc_identifier(location) is not None


def determine_movement_type(origin: str, destination: str) -> str:
    """Apply route rules independently of the AI's suggested movement label."""
    origin = normalize_location(origin)
    destination = normalize_location(destination)
    if not origin or not destination:
        return "unknown"

    origin_soc = soc_identifier(origin)
    destination_soc = soc_identifier(destination)

    if (
        origin_soc
        and destination_soc
        and origin_soc == destination_soc
        and re.search(r"\bPARKING\b", destination, flags=re.IGNORECASE)
    ):
        return "parking"
    if destination_soc:
        return "return"
    if origin_soc:
        return "outbound"
    if contains_mfm_token(destination):
        return "backload"
    return "intermediate"


def normalize_segment(
    segment: dict,
    sequence: int
) -> dict:
    origin = normalize_location(segment.get("origin"))
    destination = normalize_location(
        segment.get("destination")
    )

    suggested_type = segment.get("movement_type")
    route_type = determine_movement_type(origin, destination)

    # Route-derived classifications override model suggestions. SOC departures
    # cannot be mislabeled as backloads; non-MFM legs cannot be backloads.
    if route_type in {"outbound", "return", "backload", "parking", "unknown"}:
        movement_type = route_type
    elif suggested_type == "parking":
        movement_type = "parking"
    else:
        movement_type = route_type

    return {
        "sequence": sequence,
        "origin": origin or None,
        "destination": destination or None,
        "departure_time": segment.get("departure_time"),
        "arrival_time": segment.get("arrival_time"),
        "movement_type": movement_type,
        "source_pages": segment.get("source_pages", []),
        "review_required": (
            not origin
            or not destination
            or movement_type == "unknown"
        )
    }


def _segment_chronology_key(item):
    original_index, segment = item
    value = segment.get("departure_time") or segment.get("arrival_time")
    if not value:
        return (1, datetime.max, original_index)
    try:
        parsed = date_parser.parse(str(value), fuzzy=False)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return (0, parsed, original_index)
    except (ValueError, TypeError, OverflowError):
        return (1, datetime.max, original_index)


def process_trip(trip: dict) -> dict:
    """
    Validate and normalize one analyzed trip.
    Does not invent missing route information.
    """

    segments = trip.get("segments", [])
    indexed_segments = list(enumerate(segments))
    if any(segment.get("departure_time") or segment.get("arrival_time") for segment in segments):
        indexed_segments.sort(key=_segment_chronology_key)

    processed_segments = []

    for index, (_, segment) in enumerate(indexed_segments, start=1):
        processed_segments.append(
            normalize_segment(segment, index)
        )

    review_flags = list(
        trip.get("review_flags", [])
    )

    if not processed_segments:
        review_flags.append(
            "No movement segments were identified."
        )

    for segment in processed_segments:
        if segment["review_required"]:
            review_flags.append(
                f"Segment {segment['sequence']} needs review."
            )

    trip_date = trip.get("trip_date")

    return {
        "driver_name": trip.get("driver_name"),
        "trip_ticket_number": trip.get("trip_ticket_number"),
        "line_haul_trip_number": trip.get(
            "line_haul_trip_number"
        ),
        "trip_date": trip_date,
        "origin": normalize_location(
            trip.get("origin")
        ) or None,
        "drop_offs": [
            normalize_location(location)
            for location in trip.get("drop_offs", [])
            if normalize_location(location)
        ],
        "destination": normalize_location(
            trip.get("destination")
        ) or None,
        "return_origin": normalize_location(
            trip.get("return_origin")
        ) or None,
        "segments": processed_segments,
        "check_in_time_actual": trip.get(
            "check_in_time_actual"
        ),
        "docking_time": trip.get("docking_time"),
        "check_out_time_actual": trip.get(
            "check_out_time_actual"
        ),
        "review_flags": list(dict.fromkeys(review_flags)),
        "confidence": trip.get("confidence", "low"),
        "status": (
            "review_required"
            if review_flags
            else "ready_for_review"
        )
    }


def process_all_trips(
    analysis: dict
) -> dict:
    """
    Process all trips returned by AI analysis.
    """

    trips = analysis.get("trips", [])

    processed = [
        process_trip(trip)
        for trip in trips
    ]

    review_count = sum(
        1 for trip in processed
        if trip["status"] == "review_required"
    )

    return {
        "driver": analysis.get("driver", {}),
        "batch_id": analysis.get("batch_id"),
        "trip_count": len(processed),
        "review_required_count": review_count,
        "trips": processed,
        "approval_status": "pending_review"
    }
