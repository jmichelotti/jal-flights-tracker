"""Google Sheets client for the JAL Flight Tracker.

Three tabs:
- Snapshot: one row per (Direction, Flight Date), upserted each run.
- History: append-only log, one row per calendar cell per run.
- Alerts: append-only, one row whenever miles <= ALERT_THRESHOLD_MILES.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


SNAPSHOT_COLUMNS = [
    "Direction",
    "Flight Date",
    "Day of Week",
    "Miles",
    "Taxes",
    "Combinable",
    "Lowest Miles Ever",
    "Lowest Miles Date Seen",
    "First Seen",
    "Last Scanned",
]

HISTORY_COLUMNS = [
    "Scan Time",
    "Direction",
    "Flight Date",
    "Miles",
    "Taxes",
    "Combinable",
]

ALERT_COLUMNS = [
    "Scan Time",
    "Direction",
    "Flight Date",
    "Miles",
    "Taxes",
    "Threshold Hit",
    "Emailed",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "sheet-config.json"


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_config() -> dict:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def _snapshot_key(direction: str, flight_date: str) -> str:
    return f"{direction}|{flight_date}"


class SheetClient:
    def __init__(self):
        cfg = _load_config()
        self.cfg = cfg
        sa_path = PROJECT_ROOT / cfg["service_account_path"]
        creds = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.ss = self.gc.open_by_url(cfg["sheet_url"])
        self.snapshot_name = cfg["snapshot_tab"]
        self.history_name = cfg["history_tab"]
        self.alerts_name = cfg["alerts_tab"]

    def _ws(self, name: str):
        try:
            return self.ss.worksheet(name)
        except gspread.WorksheetNotFound:
            return self.ss.add_worksheet(title=name, rows=1000, cols=26)

    def init(self) -> dict:
        """Write headers + freeze + bold + native Table on every tab. Idempotent."""
        results = {}
        for tab, cols in [
            (self.snapshot_name, SNAPSHOT_COLUMNS),
            (self.history_name, HISTORY_COLUMNS),
            (self.alerts_name, ALERT_COLUMNS),
        ]:
            ws = self._ws(tab)
            existing = ws.row_values(1)
            if existing != cols:
                ws.update([cols], "A1", value_input_option="USER_ENTERED")
            self._format_header(ws, len(cols))
            self._ensure_table(ws, tab, len(cols))
            results[tab] = f"{len(cols)} columns ready"
        return results

    def _format_header(self, ws, ncols: int) -> None:
        ws.freeze(rows=1)
        ws.format(
            f"A1:{gspread.utils.rowcol_to_a1(1, ncols)}",
            {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.95},
            },
        )

    def _ensure_table(self, ws, tab_name: str, ncols: int) -> None:
        try:
            body = {
                "requests": [
                    {
                        "addTable": {
                            "table": {
                                "name": f"{tab_name}Table",
                                "range": {
                                    "sheetId": ws.id,
                                    "startRowIndex": 0,
                                    "endRowIndex": 1000,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": ncols,
                                },
                            }
                        }
                    }
                ]
            }
            self.ss.batch_update(body)
        except Exception:
            pass

    def _next_empty_row(self, ws) -> int:
        urls = ws.col_values(1)
        return len(urls) + 1

    def _ensure_capacity(self, ws, row: int) -> None:
        if row > ws.row_count:
            ws.add_rows(row - ws.row_count + 50)

    def read_snapshot(self) -> list[dict]:
        ws = self._ws(self.snapshot_name)
        return ws.get_all_records(expected_headers=SNAPSHOT_COLUMNS)

    def upsert_snapshot_bulk(self, cells: list[dict]) -> dict:
        """Bulk upsert snapshot rows keyed on (Direction, Flight Date).

        Each cell dict must include: Direction, Flight Date, Day of Week,
        Miles (int), Taxes (str), Combinable (bool).
        Computes / updates Lowest Miles Ever, Lowest Miles Date Seen, First Seen,
        Last Scanned automatically.
        """
        ws = self._ws(self.snapshot_name)
        today = _today()
        now = _now()

        existing_rows = ws.get_all_records(expected_headers=SNAPSHOT_COLUMNS)
        existing_by_key: dict[str, tuple[int, dict]] = {}
        for idx, row in enumerate(existing_rows, start=2):
            key = _snapshot_key(str(row.get("Direction", "")),
                                str(row.get("Flight Date", "")))
            existing_by_key[key] = (idx, row)

        updates: list[dict] = []
        inserts: list[list] = []
        inserted_count = 0
        updated_count = 0

        for cell in cells:
            direction = cell["Direction"]
            flight_date = cell["Flight Date"]
            new_miles = int(cell["Miles"]) if cell["Miles"] else 0
            key = _snapshot_key(direction, flight_date)

            if key in existing_by_key:
                row_idx, row = existing_by_key[key]
                prev_low = _to_int(row.get("Lowest Miles Ever"))
                prev_low_date = str(row.get("Lowest Miles Date Seen") or "")
                first_seen = str(row.get("First Seen") or today)

                if new_miles > 0 and (prev_low == 0 or new_miles < prev_low):
                    low_miles = new_miles
                    low_date = today
                else:
                    low_miles = prev_low if prev_low > 0 else new_miles
                    low_date = prev_low_date or today

                new_row = [
                    direction,
                    flight_date,
                    cell.get("Day of Week", ""),
                    new_miles if new_miles > 0 else "",
                    cell.get("Taxes", ""),
                    "TRUE" if cell.get("Combinable") else "FALSE",
                    low_miles if low_miles > 0 else "",
                    low_date if low_miles > 0 else "",
                    first_seen,
                    now,
                ]
                end_a1 = gspread.utils.rowcol_to_a1(row_idx, len(SNAPSHOT_COLUMNS))
                updates.append({
                    "range": f"A{row_idx}:{end_a1}",
                    "values": [new_row],
                })
                updated_count += 1
            else:
                low_miles = new_miles if new_miles > 0 else ""
                low_date = today if new_miles > 0 else ""
                new_row = [
                    direction,
                    flight_date,
                    cell.get("Day of Week", ""),
                    new_miles if new_miles > 0 else "",
                    cell.get("Taxes", ""),
                    "TRUE" if cell.get("Combinable") else "FALSE",
                    low_miles,
                    low_date,
                    today,
                    now,
                ]
                inserts.append(new_row)
                inserted_count += 1

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

        if inserts:
            start_row = self._next_empty_row(ws)
            self._ensure_capacity(ws, start_row + len(inserts))
            end_row = start_row + len(inserts) - 1
            end_a1 = gspread.utils.rowcol_to_a1(end_row, len(SNAPSHOT_COLUMNS))
            ws.update(inserts, f"A{start_row}:{end_a1}",
                      value_input_option="USER_ENTERED")

        return {"inserted": inserted_count, "updated": updated_count}

    def append_history_bulk(self, cells: list[dict]) -> dict:
        """Append raw scan rows to History tab."""
        if not cells:
            return {"appended": 0}
        ws = self._ws(self.history_name)
        now = _now()
        rows = []
        for cell in cells:
            rows.append([
                now,
                cell["Direction"],
                cell["Flight Date"],
                int(cell["Miles"]) if cell["Miles"] else "",
                cell.get("Taxes", ""),
                "TRUE" if cell.get("Combinable") else "FALSE",
            ])
        start_row = self._next_empty_row(ws)
        self._ensure_capacity(ws, start_row + len(rows))
        end_row = start_row + len(rows) - 1
        end_a1 = gspread.utils.rowcol_to_a1(end_row, len(HISTORY_COLUMNS))
        ws.update(rows, f"A{start_row}:{end_a1}",
                  value_input_option="USER_ENTERED")
        return {"appended": len(rows)}

    def append_alerts(self, alerts: list[dict]) -> dict:
        """Append alert rows when miles <= threshold."""
        if not alerts:
            return {"appended": 0}
        ws = self._ws(self.alerts_name)
        now = _now()
        rows = []
        for a in alerts:
            rows.append([
                now,
                a["Direction"],
                a["Flight Date"],
                int(a["Miles"]) if a["Miles"] else "",
                a.get("Taxes", ""),
                a["Threshold Hit"],
                "",
            ])
        start_row = self._next_empty_row(ws)
        self._ensure_capacity(ws, start_row + len(rows))
        end_row = start_row + len(rows) - 1
        end_a1 = gspread.utils.rowcol_to_a1(end_row, len(ALERT_COLUMNS))
        ws.update(rows, f"A{start_row}:{end_a1}",
                  value_input_option="USER_ENTERED")
        return {"appended": len(rows)}


def _to_int(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0
