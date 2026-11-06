
import os
import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID")

SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "credentials/service-account.json"
)

TRIP_SHEET_NAME = os.getenv(
    "TRIP_SHEET_NAME",
    "Trip Encoding"
)

TOLL_SHEET_NAME = os.getenv(
    "TOLL_SHEET_NAME",
    "Toll Reimbursements"
)

PARKING_SHEET_NAME = os.getenv(
    "PARKING_SHEET_NAME",
    "Parking Reimbursements"
)


def get_sheets_service():
    """Authenticate using a Google service account."""

    if not SPREADSHEET_ID:
        raise RuntimeError(
            "GOOGLE_SPREADSHEET_ID is not configured."
        )

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise RuntimeError(
            "Google service account credentials were not found."
        )

    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES
    )

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False
    )


def append_rows(
    service,
    sheet_name: str,
    rows: list[list[Any]]
) -> dict:
    """Append rows to a specified worksheet."""

    if not rows:
        return {
            "sheet": sheet_name,
            "rows_added": 0
        }

    body = {
        "values": rows
    }

    result = service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

    return {
        "sheet": sheet_name,
        "rows_added": result.get(
            "updates", {}
        ).get("updatedRows", 0)
    }


def prepare_trip_rows(result: dict) -> list[list[Any]]:
    """Prepare trip records for the provisional worksheet."""

    rows = []

    for trip in result.get("trips", []):
        for segment in trip.get("segments", []):
            rows.append([
                result.get("driver", {}).get("name"),
                trip.get("trip_date"),
                trip.get("trip_ticket_number"),
                trip.get("line_haul_trip_number"),
                segment.get("sequence"),
                segment.get("origin"),
                segment.get("destination"),
                segment.get("departure_time"),
                segment.get("arrival_time"),
                segment.get("movement_type"),
                trip.get("check_in_time_actual"),
                trip.get("docking_time"),
                trip.get("check_out_time_actual"),
                "APPROVED"
            ])

    return rows


def prepare_reimbursement_rows(
    records: list[dict],
    driver_name: str
) -> list[list[Any]]:
    """Prepare reimbursement records for spreadsheet export."""

    rows = []

    for record in records:
        rows.append([
            driver_name,
            record.get("date"),
            record.get("location"),
            record.get("amount"),
            record.get("receipt_reference"),
            ",".join(
                str(page)
                for page in record.get("source_pages", [])
            ),
            "APPROVED"
        ])

    return rows


async def export_approved_batch(
    batch_id: str,
    result: dict
) -> dict:
    """
    Export approved records to Google Sheets.

    This is a provisional export layout.
    It must be mapped to the user's actual TENGS
    spreadsheet before production use.
    """

    if result.get("approval_status") != "approved":
        raise ValueError(
            "Batch must be approved before export."
        )

    service = get_sheets_service()

    driver_name = (
        result.get("driver", {}).get("name")
        or "UNKNOWN"
    )

    trip_rows = prepare_trip_rows(result)

    toll_rows = prepare_reimbursement_rows(
        result.get("toll_reimbursements", []),
        driver_name
    )

    parking_rows = prepare_reimbursement_rows(
        result.get("parking_reimbursements", []),
        driver_name
    )

    exports = []

    try:
        exports.append(
            append_rows(
                service,
                TRIP_SHEET_NAME,
                trip_rows
            )
        )

        exports.append(
            append_rows(
                service,
                TOLL_SHEET_NAME,
                toll_rows
            )
        )

        exports.append(
            append_rows(
                service,
                PARKING_SHEET_NAME,
                parking_rows
            )
        )

    except HttpError:
        logger.exception(
            "Google Sheets export failed for batch %s",
            batch_id
        )
        raise

    return {
        "batch_id": batch_id,
        "driver": driver_name,
        "exports": exports,
        "status": "exported"
    }
