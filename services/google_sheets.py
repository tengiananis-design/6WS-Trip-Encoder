import os
import json
import logging
from typing import Any
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEETS_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service-account.json")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

TRIP_SHEET_NAME = os.getenv("TRIP_SHEET_NAME", "Trip Encoding")
TOLL_SHEET_NAME = os.getenv("TOLL_SHEET_NAME", "Toll Reimbursements")
PARKING_SHEET_NAME = os.getenv("PARKING_SHEET_NAME", "Parking Reimbursements")

def get_sheets_service():
    if not SPREADSHEET_ID:
        raise RuntimeError("Set GOOGLE_SPREADSHEET_ID (or GOOGLE_SHEETS_ID).")
    if SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as error:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from error
        credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif os.path.exists(SERVICE_ACCOUNT_FILE):
        credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    else:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or provide a service-account credentials file.")
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)

def append_rows(service, sheet_name: str, rows: list[list[Any]]) -> dict:
    if not rows:
        return {"sheet": sheet_name, "rows_added": 0}
    result = service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()
    return {"sheet": sheet_name, "rows_added": result.get("updates", {}).get("updatedRows", 0)}

def prepare_trip_rows(result: dict) -> list[list[Any]]:
    rows = []
    for trip in result.get("trips", []):
        for segment in trip.get("segments", []):
            rows.append([
                result.get("driver", {}).get("name"), trip.get("trip_date"),
                trip.get("trip_ticket_number"), trip.get("line_haul_trip_number"),
                segment.get("sequence"), segment.get("origin"), segment.get("destination"),
                segment.get("departure_time"), segment.get("arrival_time"),
                segment.get("movement_type"), trip.get("check_in_time_actual"),
                trip.get("docking_time"), trip.get("check_out_time_actual"), "APPROVED"
            ])
    return rows

def prepare_reimbursement_rows(records: list[dict], driver_name: str) -> list[list[Any]]:
    return [[driver_name, record.get("date"), record.get("location"), record.get("amount"),
             record.get("receipt_reference"), ",".join(str(p) for p in record.get("source_pages", [])),
             "APPROVED"] for record in records]

async def export_approved_batch(batch_id: str, result: dict) -> dict:
    raise RuntimeError("Export is intentionally disabled until the real TENGS sheet columns, approval persistence, and duplicate protection are implemented.")
