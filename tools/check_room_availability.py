"""Room availability lookup for uploaded Excel and PDF schedules."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber
from langchain.tools import tool


SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".pdf"}

DAYS = {
    "sun": "sunday",
    "sunday": "sunday",
    "mon": "monday",
    "monday": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "tuesday": "tuesday",
    "wed": "wednesday",
    "wednesday": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "thursday": "thursday",
    "fri": "friday",
    "friday": "friday",
    "sat": "saturday",
    "saturday": "saturday",
}

COLUMN_ALIASES = {
    "room": {"room", "room id", "room name", "classroom", "venue"},
    "day": {"day", "weekday", "day of week"},
    "date": {"date", "booking date", "session date", "event date"},
    "start": {"start", "start time", "from", "time from"},
    "end": {"end", "end time", "to", "time to"},
    "status": {"status", "availability", "availability status", "room status"},
    "capacity": {"capacity", "room capacity", "seats", "seat capacity"},
    "type": {"type", "room type", "venue type"},
    "building": {"building", "building name", "location"},
    "floor": {"floor", "floor number", "level"},
    "features": {"features", "equipment", "facilities", "room features"},
    "week": {"week", "academic week", "semester week"},
    "season": {"season", "term type"},
    "booking_id": {"booking id", "reservation id", "event id"},
}

FREE_STATUSES = {
    "available",
    "free",
    "open",
    "vacant",
    "unreserved",
    "cancelled",
    "canceled",
}

INACTIVE_STATUSES = {
    "inactive",
    "closed",
    "maintenance",
    "unavailable",
    "out of service",
}


class RoomAvailabilityInputError(ValueError):
    """An input problem that must be returned to the UI for clarification."""

    def __init__(self, summary: str, required_action: str) -> None:
        super().__init__(summary)
        self.summary = summary
        self.required_action = required_action


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str:
    return "" if _is_blank(value) else str(value).strip()


def _normalise(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).casefold()).strip()


def _canonical_column(value: Any) -> str | None:
    label = _normalise(value)
    return next(
        (name for name, aliases in COLUMN_ALIASES.items() if label in aliases),
        None,
    )


def _find_header_row(raw: pd.DataFrame) -> int | None:
    best: tuple[int, int] | None = None
    for row_index in range(min(20, len(raw))):
        names = [_canonical_column(value) for value in raw.iloc[row_index].tolist()]
        score = len({name for name in names if name})
        if "room" in names and score >= 2 and (best is None or score > best[1]):
            best = (row_index, score)
    return best[0] if best else None


def _prepare_table(raw: pd.DataFrame, source: str) -> pd.DataFrame | None:
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return None

    header_row = _find_header_row(raw)
    if header_row is None:
        return None

    counts: Counter[str] = Counter()
    columns: list[str] = []
    for index, value in enumerate(raw.iloc[header_row].tolist()):
        base = _canonical_column(value) or f"unused_{index}"
        counts[base] += 1
        columns.append(base if counts[base] == 1 else f"{base}_{counts[base]}")

    table = raw.iloc[header_row + 1 :].copy()
    table.columns = columns
    table = table.dropna(how="all")
    table = table[table["room"].map(lambda value: bool(_text(value)))]
    table = table[table["room"].map(_normalise) != "room"]
    table["_source"] = source
    return table.reset_index(drop=True)


def _load_excel(path: Path) -> list[pd.DataFrame]:
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    except Exception as error:
        raise RoomAvailabilityInputError(
            "The uploaded Excel room schedule could not be read.",
            f"Upload a valid, unencrypted Excel file. Reader detail: {error}",
        ) from error

    return [
        table
        for name, raw in sheets.items()
        if (table := _prepare_table(raw, str(name))) is not None
    ]


def _load_pdf(path: Path) -> list[pd.DataFrame]:
    tables: list[pd.DataFrame] = []
    try:
        with pdfplumber.open(path) as document:
            for page_number, page in enumerate(document.pages, start=1):
                for table_number, values in enumerate(page.extract_tables(), start=1):
                    source = f"PDF page {page_number}, table {table_number}"
                    table = _prepare_table(pd.DataFrame(values), source)
                    if table is not None:
                        tables.append(table)
    except Exception as error:
        raise RoomAvailabilityInputError(
            "The uploaded PDF room schedule could not be read.",
            f"Upload an unencrypted PDF containing selectable schedule tables. Reader detail: {error}",
        ) from error
    return tables


def _load_tables(file_path: str) -> tuple[Path, list[pd.DataFrame]]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise RoomAvailabilityInputError(
            "The uploaded room schedule file was not found.",
            "Upload the file again and provide its current path.",
        )
    if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise RoomAvailabilityInputError(
            "The uploaded room schedule format is unsupported.",
            "Upload an Excel (.xlsx or .xls) or PDF (.pdf) file.",
        )

    tables = _load_pdf(path) if path.suffix.casefold() == ".pdf" else _load_excel(path)
    if not tables:
        raise RoomAvailabilityInputError(
            "No reliable room table was found in the uploaded file.",
            "Provide a table with a Room column and room inventory or booking-time columns.",
        )
    return path, tables


def _parse_day_or_date(value: str) -> tuple[str, date | None]:
    normalised = _normalise(value)
    if normalised in DAYS:
        return DAYS[normalised], None
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as error:
        raise RoomAvailabilityInputError(
            "The requested day or date is invalid or ambiguous.",
            "Use a full weekday name or an ISO date in YYYY-MM-DD format.",
        ) from error
    return parsed.strftime("%A").casefold(), parsed


def _parse_time(value: Any, label: str) -> int:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isnan(float(value)) and 0 <= float(value) < 1:
            return round(float(value) * 24 * 60)

    text = _text(value).upper().replace(".", "")
    for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            parsed = datetime.strptime(text, time_format)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    raise RoomAvailabilityInputError(
        f"The {label} time is invalid or ambiguous.",
        "Use a 24-hour HH:MM value such as 08:30 or 15:45.",
    )


def _day_matches(row: pd.Series, weekday: str, exact_date: date | None) -> bool:
    if "date" in row.index and _text(row.get("date")):
        parsed = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(parsed):
            return False
        return parsed.date() == exact_date if exact_date else parsed.day_name().casefold() == weekday
    return DAYS.get(_normalise(row.get("day"))) == weekday


def _week_matches(row: pd.Series, academic_week: int | None) -> bool:
    if "week" not in row.index or not _text(row.get("week")):
        return True
    if academic_week is None:
        return False
    try:
        return int(float(row.get("week"))) == academic_week
    except (TypeError, ValueError):
        return False


def _number(value: Any) -> int | None:
    try:
        return None if _is_blank(value) else int(float(value))
    except (TypeError, ValueError):
        return None


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _inventory_and_schedules(
    tables: list[pd.DataFrame],
) -> tuple[dict[str, dict[str, Any]], list[pd.DataFrame], list[str]]:
    inventory_tables = [
        table for table in tables if not {"start", "end"}.issubset(table.columns)
    ]
    schedules = [
        table
        for table in tables
        if {"room", "start", "end"}.issubset(table.columns)
        and ("day" in table.columns or "date" in table.columns)
    ]
    if not schedules:
        raise RoomAvailabilityInputError(
            "No valid room booking schedule was found.",
            "Provide Room, Day or Date, Start, and End columns.",
        )

    inventory: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for table in inventory_tables:
        for _, row in table.iterrows():
            room = _text(row.get("room"))
            if not room:
                continue
            metadata = inventory.setdefault(room.casefold(), {"room": room})
            for field in ("type", "capacity", "building", "floor", "features", "status"):
                if field in row.index and _text(row.get(field)):
                    metadata.setdefault(
                        field,
                        _number(row.get(field)) if field == "capacity" else _text(row.get(field)),
                    )

    scheduled_metadata: dict[str, dict[str, Any]] = {}
    for table in schedules:
        for _, row in table.iterrows():
            room = _text(row.get("room"))
            if not room:
                continue
            room_key = room.casefold()
            schedule_values = scheduled_metadata.setdefault(room_key, {"room": room})
            for field in ("type", "capacity", "building", "floor", "features"):
                if field in row.index and _text(row.get(field)):
                    schedule_values.setdefault(
                        field,
                        _number(row.get(field)) if field == "capacity" else _text(row.get(field)),
                    )
            if room_key in inventory:
                for field, value in schedule_values.items():
                    inventory[room_key].setdefault(field, value)

    if not inventory:
        inventory = scheduled_metadata
        warnings.append("No separate room inventory was found; returned metadata may be limited.")
    else:
        missing = set(scheduled_metadata) - set(inventory)
        if missing:
            warnings.append(
                f"{len(missing)} scheduled room(s) were absent from the inventory and excluded."
            )

    return inventory, schedules, warnings


def _calculate_availability(
    uploaded_file_path: str,
    requested_day_or_date: str,
    requested_start: str,
    requested_end: str,
    academic_week: int | None,
    minimum_capacity: int,
    required_features: list[str] | None,
    room_types: list[str] | None,
) -> dict[str, Any]:
    path, tables = _load_tables(uploaded_file_path)
    weekday, exact_date = _parse_day_or_date(requested_day_or_date)
    start = _parse_time(requested_start, "requested start")
    end = _parse_time(requested_end, "requested end")

    if end <= start:
        raise RoomAvailabilityInputError(
            "The requested period is invalid.",
            "Provide an end time later than the start time on the same day.",
        )
    if academic_week is not None and not 1 <= academic_week <= 53:
        raise RoomAvailabilityInputError(
            "The academic week is invalid.",
            "Provide an academic week number between 1 and 53.",
        )
    if minimum_capacity < 0:
        raise RoomAvailabilityInputError(
            "The minimum capacity cannot be negative.",
            "Provide zero or a positive number of seats.",
        )

    inventory, schedules, warnings = _inventory_and_schedules(tables)
    has_week_data = any(
        "week" in table.columns
        and table["week"].map(lambda value: bool(_text(value))).any()
        for table in schedules
    )
    if has_week_data and academic_week is None:
        raise RoomAvailabilityInputError(
            "The schedule contains week-specific exceptions, so room availability cannot be guaranteed without the academic week.",
            "Provide academic_week for the requested compensation period.",
        )

    has_finals = any(
        "season" in table.columns
        and table["season"].map(_normalise).eq("finals").any()
        for table in schedules
    )
    finals_mode = bool(academic_week is not None and academic_week >= 13 and has_finals)

    blocked: dict[str, int] = defaultdict(int)
    covered: set[str] = set()
    uncertain: set[str] = set()
    explicit_availability = False
    invalid_rows = 0

    for table in schedules:
        week_specific = "week" in table.columns or "season" in table.columns
        source = _normalise(table["_source"].iloc[0])
        is_exception = week_specific or "exception" in source or "exam" in source

        for _, row in table.iterrows():
            if not _day_matches(row, weekday, exact_date) or not _week_matches(row, academic_week):
                continue

            room_key = _text(row.get("room")).casefold()
            if room_key not in inventory:
                continue
            try:
                row_start = _parse_time(row.get("start"), "schedule start")
                row_end = _parse_time(row.get("end"), "schedule end")
            except RoomAvailabilityInputError:
                uncertain.add(room_key)
                invalid_rows += 1
                continue
            if row_end <= row_start:
                uncertain.add(room_key)
                invalid_rows += 1
                continue

            status = _normalise(row.get("status")) if "status" in row.index else ""
            free_row = status in FREE_STATUSES
            overlaps = start < row_end and end > row_start
            contains = row_start <= start and row_end >= end

            if free_row:
                explicit_availability = True
            if contains and (free_row or (finals_mode and not is_exception)):
                covered.add(room_key)
            if not overlaps:
                continue
            if finals_mode and not is_exception:
                continue
            if not free_row:
                blocked[room_key] += 1

    if invalid_rows:
        warnings.append(
            f"{invalid_rows} relevant schedule row(s) had invalid times; their rooms were excluded."
        )

    feature_terms = [_normalise(value) for value in (required_features or []) if _normalise(value)]
    accepted_types = {_normalise(value) for value in (room_types or []) if _normalise(value)}
    excluded: Counter[str] = Counter()
    available: list[dict[str, Any]] = []

    for room_key, metadata in inventory.items():
        if _normalise(metadata.get("status")) in INACTIVE_STATUSES:
            excluded["inactive"] += 1
            continue
        if room_key in uncertain:
            excluded["uncertain_schedule_data"] += 1
            continue
        if blocked[room_key]:
            excluded["time_conflict"] += 1
            continue
        if explicit_availability and room_key not in covered:
            excluded["outside_available_period"] += 1
            continue

        capacity = _number(metadata.get("capacity"))
        if minimum_capacity and capacity is None:
            excluded["capacity_unknown"] += 1
            continue
        if minimum_capacity and capacity is not None and capacity < minimum_capacity:
            excluded["insufficient_capacity"] += 1
            continue
        if accepted_types and _normalise(metadata.get("type")) not in accepted_types:
            excluded["room_type_mismatch"] += 1
            continue

        features = _normalise(metadata.get("features"))
        if feature_terms and not features:
            excluded["features_unknown"] += 1
            continue
        if any(term not in features for term in feature_terms):
            excluded["missing_features"] += 1
            continue

        available.append(
            {
                "room": metadata.get("room"),
                "type": metadata.get("type"),
                "capacity": capacity,
                "building": metadata.get("building"),
                "floor": metadata.get("floor"),
                "features": metadata.get("features"),
            }
        )

    available.sort(
        key=lambda item: (
            item["capacity"] if item["capacity"] is not None else math.inf,
            _natural_key(str(item["room"])),
        )
    )

    return {
        "status": "success",
        "summary": f"{len(available)} room(s) are available for the complete requested period.",
        "request": {
            "day_or_date": requested_day_or_date,
            "resolved_weekday": weekday.title(),
            "start": requested_start,
            "end": requested_end,
            "academic_week": academic_week,
            "minimum_capacity": minimum_capacity,
            "required_features": required_features or [],
            "room_types": room_types or [],
        },
        "source_file": path.name,
        "finals_mode": finals_mode,
        "available_room_count": len(available),
        "available_rooms": available,
        "excluded_counts": dict(sorted(excluded.items())),
        "warnings": sorted(set(warnings)),
    }


@tool
def check_room_availability(
    uploaded_file_path: str,
    requested_day_or_date: str,
    requested_start: str,
    requested_end: str,
    academic_week: int | None = None,
    minimum_capacity: int = 0,
    required_features: list[str] | None = None,
    room_types: list[str] | None = None,
) -> str:
    """Return every suitable room free for a complete requested period.

    Read room inventory and bookings from an uploaded Excel or PDF file. Check
    recurring availability and week-specific exceptions, then return rooms that
    are free for the full interval. Optional capacity, feature, and room-type
    filters help the repair tool choose the best compensation room.

    Use a weekday or ISO ``YYYY-MM-DD`` date and 24-hour ``HH:MM`` times. Supply
    ``academic_week`` whenever the uploaded schedule has week-specific data.
    The tool requests clarification rather than guessing when data is ambiguous.
    """
    try:
        payload = _calculate_availability(
            uploaded_file_path=uploaded_file_path,
            requested_day_or_date=requested_day_or_date,
            requested_start=requested_start,
            requested_end=requested_end,
            academic_week=academic_week,
            minimum_capacity=minimum_capacity,
            required_features=required_features,
            room_types=room_types,
        )
    except RoomAvailabilityInputError as error:
        payload = {
            "status": "information_required",
            "summary": error.summary,
            "required_action": error.required_action,
            "available_rooms": [],
        }
    except Exception as error:
        payload = {
            "status": "error",
            "summary": "Room availability could not be checked safely.",
            "required_action": "Review the uploaded schedule or contact the system administrator.",
            "error": f"{type(error).__name__}: {error}",
            "available_rooms": [],
        }
    return json.dumps(payload, ensure_ascii=False, default=str)
