
import json
import os
import logging
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SPREADSHEET_ID = (
    os.getenv("GOOGLE_SHEETS_ID")
    or os.getenv("GOOGLE_SPREADSHEET_ID")
)

SERVICE_ACCOUNT_SETTING = (
    os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    or "credentials/service-account.json"
)

TRIP_SHEET_NAME = os.getenv("TRIP_SHEET_NAME", "Trip Encoding")
TOLL_SHEET_NAME = os.getenv("TOLL_SHEET_NAME", "Toll Reimbursements")
PARKING_SHEET_NAME = os.getenv("PARKING_SHEET_NAME", "Parking Reimbursements")


def get_sheets_service():
    """Authenticate from a service-account JSON path or JSON environment value."""
    if not SPREADSHEET_ID:
        raise RuntimeError("GOOGLE_SHEETS_ID is not configured.")

    setting = SERVICE_ACCOUNT_SETTING.strip()
    if setting.startswith("{"):
        try:
            info = json.loads(setting)
        except json.JSONDecodeError as error:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from error
        credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        credential_path = Path(setting)
        if not credential_path.is_file():
            raise RuntimeError("Google service-account JSON was not found. Set GOOGLE_SERVICE_ACCOUNT_JSON to JSON contents or a file path.")
        credentials = service_account.Credentials.from_service_account_file(str(credential_path), scopes=SCOPES)

    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def append_rows(service, sheet_name: str, rows: list[list[Any]]) -> dict:
    if not rows:
        return {"sheet": sheet_name, "rows_added": 0}
    result = service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()
    return {
        "sheet": sheet_name,
        "rows_added": result.get("updates", {}).get("updatedRows", 0)
    }


def prepare_trip_rows(result: dict) -> list[list[Any]]:
    rows = []
    for trip in result.get("trips", []):
        for segment in trip.get("segments", []):
            rows.append([
                trip.get("driver_name") or result.get("driver", {}).get("name"),
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


def prepare_reimbursement_rows(records: list[dict], driver_name: str) -> list[list[Any]]:
    return [
        [
            record.get("driver_name") or driver_name,
            record.get("date"),
            record.get("location"),
            record.get("amount"),
            record.get("receipt_reference"),
            ",".join(str(page) for page in record.get("source_pages", [])),
            "APPROVED"
        ]
        for record in records
    ]


async def export_approved_batch(batch_id: str, result: dict) -> dict:
    if result.get("approval_status") != "approved":
        raise ValueError("Batch must be approved before export.")

    service = get_sheets_service()
    driver_name = result.get("driver", {}).get("name") or "UNKNOWN"
    exports = []
    try:
        exports.append(append_rows(service, TRIP_SHEET_NAME, prepare_trip_rows(result)))
        exports.append(append_rows(service, TOLL_SHEET_NAME, prepare_reimbursement_rows(result.get("toll_reimbursements", []), driver_name)))
        exports.append(append_rows(service, PARKING_SHEET_NAME, prepare_reimbursement_rows(result.get("parking_reimbursements", []), driver_name)))
    except HttpError:
        logger.exception("Google Sheets export failed for batch %s", batch_id)
        raise

    return {"batch_id": batch_id, "driver": driver_name, "exports": exports, "status": "exported"}
