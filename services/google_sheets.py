import os
import json
import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID") or os.getenv("GOOGLE_SHEETS_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service-account.json")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# Exact worksheet names and column order taken from the user's TENGS.xlsx workbook.
SUMMARY_SHEET_NAME = os.getenv("TENGS_SUMMARY_SHEET", "SUMMARY")
TOLL_SHEET_NAME = os.getenv("TENGS_TOLL_SHEET", "TOLL")
PARKING_SHEET_NAME = os.getenv("TENGS_PARKING_SHEET", "PARKING")
PASSWAY_SHEET_NAME = os.getenv("TENGS_PASSWAY_SHEET", "PASSWAY")

SUMMARY_HEADERS = [
    "CHECK IN TIME (ACTUAL)", "VALID TIME (circled)", "DOCKING TIME",
    "DOCKING DATE OR CHECK OUT DATE", "CHECK OUT TIME", "DATE OF TRIP",
    "LINEHAUL TRIP NO", "ID NO", "DRIVER'S NAME", "PLATENO.", "FLEET TYPE",
    "ORIGIN", "DROP OFFS", "DESTINATION", "WITH BACKLOAD",
    "DEMMURAGE AVAILMENT", "REMARKS", "LATE"
]
TOLL_HEADERS = [
    "DATE", "LINEHAULTRIP", "ID NO", "DRIVER'S NAME", "PLATE NO.",
    "FLEET TYPE", "ORIGIN", "DESTINATION", "RFID TOLL CHARGES",
    "ENTRY POINT", "EXIT POINT", "DATE OF O.R.", "TAG NUMBER", "AMOUNT"
]
EXPENSE_HEADERS = [
    "DATE OF TRIP", "LINEHAUL TRIP NO", "ID NO", "DRIVER'S NAME",
    "PLATE NO.", "FLEET TYPE", "ORIGIN", "DESTINATION", "VENDORS NAME",
    "ADDRESS", "DATE OF O.R.", "TAG NUMBER", "AMOUNT"
]


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


def _driver_value(driver: Any, key: str):
    return driver.get(key) if isinstance(driver, dict) else None


def prepare_summary_rows(result: dict) -> list[list[Any]]:
    rows = []
    for driver_result in result.get("drivers", []):
        driver = driver_result.get("driver", {})
        for trip in driver_result.get("trips", []):
            rows.append([
                trip.get("check_in_time_actual"),
                trip.get("valid_time_circled"),
                trip.get("docking_time"),
                trip.get("docking_date_or_check_out_date"),
                trip.get("check_out_time"),
                trip.get("trip_date"),
                trip.get("line_haul_trip_number"),
                trip.get("driver_id") or _driver_value(driver, "id"),
                trip.get("driver_name") or _driver_value(driver, "name"),
                trip.get("plate_no"),
                trip.get("fleet_type"),
                trip.get("origin"),
                " / ".join(trip.get("drop_offs", []) or []),
                trip.get("destination"),
                trip.get("with_backload"),
                trip.get("demurrage_availment"),
                trip.get("remarks"),
                trip.get("late"),
            ])
    return rows


def prepare_toll_rows(result: dict) -> list[list[Any]]:
    rows = []
    for driver_result in result.get("drivers", []):
        for record in driver_result.get("toll_reimbursements", []):
            rows.append([
                record.get("date") or record.get("trip_date"),
                record.get("line_haul_trip_number"),
                record.get("driver_id"),
                record.get("driver_name") or _driver_value(driver_result.get("driver"), "name"),
                record.get("plate_no"),
                record.get("fleet_type"),
                record.get("origin"),
                record.get("destination"),
                record.get("rfid_toll_charges"),
                record.get("entry_point"),
                record.get("exit_point"),
                record.get("date_of_or"),
                record.get("tag_number"),
                record.get("amount"),
            ])
    return rows


def _prepare_vendor_rows(result: dict, field: str) -> list[list[Any]]:
    rows = []
    for driver_result in result.get("drivers", []):
        for record in driver_result.get(field, []):
            rows.append([
                record.get("trip_date"),
                record.get("line_haul_trip_number"),
                record.get("driver_id"),
                record.get("driver_name") or _driver_value(driver_result.get("driver"), "name"),
                record.get("plate_no"),
                record.get("fleet_type"),
                record.get("origin"),
                record.get("destination"),
                record.get("vendor_name"),
                record.get("address"),
                record.get("date_of_or"),
                record.get("tag_number"),
                record.get("amount"),
            ])
    return rows


def prepare_parking_rows(result: dict) -> list[list[Any]]:
    return _prepare_vendor_rows(result, "parking_reimbursements")


def prepare_passway_rows(result: dict) -> list[list[Any]]:
    return _prepare_vendor_rows(result, "passway_reimbursements")


def prepare_tengs_rows(result: dict) -> dict[str, list[list[Any]]]:
    """Prepare exact-width rows for the TENGS workbook. Does not write to Sheets."""
    return {
        SUMMARY_SHEET_NAME: prepare_summary_rows(result),
        TOLL_SHEET_NAME: prepare_toll_rows(result),
        PARKING_SHEET_NAME: prepare_parking_rows(result),
        PASSWAY_SHEET_NAME: prepare_passway_rows(result),
    }


async def export_approved_batch(batch_id: str, result: dict) -> dict:
    raise RuntimeError(
        "Export remains disabled until persistent human approval, the live Google Sheet "
        "is verified, and duplicate-safe write behavior is implemented."
    )
