import asyncio
import json
import os
import logging
import threading
from datetime import datetime, timezone
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
EXPORT_LEDGER_SHEET = os.getenv("EXPORT_LEDGER_SHEET", "_6WS Export Ledger")
_export_lock = threading.Lock()


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


def _cell_data(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def _append_cells_request(sheet_id: int, rows: list[list[Any]]) -> dict | None:
    if not rows:
        return None
    return {
        "appendCells": {
            "sheetId": sheet_id,
            "rows": [{"values": [_cell_data(value) for value in row]} for row in rows],
            "fields": "userEnteredValue"
        }
    }


def _sheet_ids(service) -> dict[str, int]:
    metadata = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields="sheets(properties(sheetId,title))"
    ).execute()
    return {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in metadata.get("sheets", [])
    }


def _ensure_ledger_sheet(service, sheet_ids: dict[str, int]) -> dict[str, int]:
    if EXPORT_LEDGER_SHEET not in sheet_ids:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": EXPORT_LEDGER_SHEET}}}]}
        ).execute()
        sheet_ids = _sheet_ids(service)
    header = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{EXPORT_LEDGER_SHEET}'!A1:G1"
    ).execute().get("values", [])
    if not header:
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{EXPORT_LEDGER_SHEET}'!A1:G1",
            valueInputOption="RAW",
            body={"values": [["Batch ID", "Exported At (UTC)", "Driver", "Trip Rows", "Toll Rows", "Parking Rows", "Status"]]}
        ).execute()
    return sheet_ids


def _existing_export(service, batch_id: str) -> bool:
    response = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{EXPORT_LEDGER_SHEET}'!A:A"
    ).execute()
    return any(
        row and str(row[0]).strip() == batch_id
        for row in response.get("values", [])
    )


def _export_approved_batch_sync(batch_id: str, result: dict) -> dict:
    if result.get("approval_status") != "approved":
        raise ValueError("Batch must be approved before export.")

    with _export_lock:
        service = get_sheets_service()
        sheet_ids = _ensure_ledger_sheet(service, _sheet_ids(service))
        required = [TRIP_SHEET_NAME, TOLL_SHEET_NAME, PARKING_SHEET_NAME]
        missing = [name for name in required if name not in sheet_ids]
        if missing:
            raise RuntimeError("Google Sheets is missing required tab(s): " + ", ".join(missing))

        if _existing_export(service, batch_id):
            return {
                "batch_id": batch_id,
                "status": "already_exported",
                "message": "This batch ID is already recorded in the export ledger; no rows were added."
            }

        driver_name = result.get("driver", {}).get("name") or "UNKNOWN"
        trip_rows = prepare_trip_rows(result)
        toll_rows = prepare_reimbursement_rows(result.get("toll_reimbursements", []), driver_name)
        parking_rows = prepare_reimbursement_rows(result.get("parking_reimbursements", []), driver_name)
        counts = {
            TRIP_SHEET_NAME: len(trip_rows),
            TOLL_SHEET_NAME: len(toll_rows),
            PARKING_SHEET_NAME: len(parking_rows)
        }
        ledger_row = [[
            batch_id,
            datetime.now(timezone.utc).isoformat(),
            driver_name,
            counts[TRIP_SHEET_NAME],
            counts[TOLL_SHEET_NAME],
            counts[PARKING_SHEET_NAME],
            "EXPORTED"
        ]]

        # All three output tabs and the ledger are written in one atomic Sheets
        # batchUpdate. A retry sees the ledger entry and cannot append twice.
        requests = [
            request for request in (
                _append_cells_request(sheet_ids[TRIP_SHEET_NAME], trip_rows),
                _append_cells_request(sheet_ids[TOLL_SHEET_NAME], toll_rows),
                _append_cells_request(sheet_ids[PARKING_SHEET_NAME], parking_rows),
                _append_cells_request(sheet_ids[EXPORT_LEDGER_SHEET], ledger_row)
            ) if request
        ]
        try:
            service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": requests}
            ).execute()
        except HttpError:
            logger.exception("Atomic Google Sheets export failed for batch %s", batch_id)
            raise

        return {
            "batch_id": batch_id,
            "driver": driver_name,
            "exports": [
                {"sheet": name, "rows_added": counts[name]}
                for name in required
            ],
            "status": "exported"
        }


async def export_approved_batch(batch_id: str, result: dict) -> dict:
    return await asyncio.to_thread(_export_approved_batch_sync, batch_id, result)
