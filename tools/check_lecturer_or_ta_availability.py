"""Lecturer and teaching-assistant availability checking tool."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from typing import Any
from langchain.tools import tool
from .get_schedule import get_schedule


MAX_SCHEDULE_ROWS = 500
MAX_SCHEDULE_CHARS = 120_000

DAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

DAY_KEYS = {"day", "weekday", "dayofweek", "sessionday", "scheduleday"}
DATE_KEYS = {"date", "sessiondate", "scheduleddate", "scheduledate"}
START_KEYS = {
    "start",
    "starttime",
    "startdatetime",
    "sessionstart",
    "scheduledstart",
    "from",
    "begins",
    "beginstime",
}
END_KEYS = {
    "end",
    "endtime",
    "enddatetime",
    "sessionend",
    "scheduledend",
    "to",
    "ends",
    "endstime",
}
WEEK_KEYS = {
    "week",
    "weeks",
    "weeknumber",
    "academicweek",
    "academicweeknumber",
    "teachingweek",
}
STATUS_KEYS = {"status", "sessionstatus", "bookingstatus"}
STAFF_ID_KEYS = {
    "staffid",
    "staffids",
    "lecturerid",
    "lecturerids",
    "doctorid",
    "doctorids",
    "facultyid",
    "facultyids",
    "taid",
    "taids",
    "teachingassistantid",
    "teachingassistantids",
    "instructorid",
    "instructorids",
}
STAFF_NAME_KEYS = {
    "staff",
    "staffname",
    "staffnames",
    "staffmember",
    "lecturer",
    "lecturers",
    "lecturername",
    "lecturerta",
    "lecturerorta",
    "doctor",
    "doctors",
    "doctorname",
    "ta",
    "tas",
    "taname",
    "teachingassistant",
    "teachingassistants",
    "teachingassistantname",
    "instructor",
    "instructors",
    "instructorname",
}

CANCELLED_STATES = {
    "cancelled",
    "canceled",
    "deleted",
    "inactive",
    "postponed",
    "removed",
}
FREE_STATES = {"available", "free", "open"}


class AvailabilityInputError(ValueError):
    """Raised when the availability request is invalid or incomplete."""


class ScheduleLookupError(RuntimeError):
    """Raised when get_schedule cannot provide reliable schedule data."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalise_identity(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _normalised_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        _normalise_key(key): value
        for key, value in record.items()
        if not str(key).startswith("_")
    }


def _first_value(record: dict[str, Any], keys: set[str]) -> Any:
    normalised = _normalised_record(record)
    for key in keys:
        value = normalised.get(key)
        if value not in (None, ""):
            return value
    return None


def _values_for_keys(record: dict[str, Any], keys: set[str]) -> list[str]:
    normalised = _normalised_record(record)
    values: list[str] = []
    for key in keys:
        value = normalised.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            values.extend(str(item).strip() for item in value.values() if str(item).strip())
        else:
            values.extend(
                part.strip()
                for part in re.split(r"[;,|\n]+", str(value))
                if part.strip()
            )
    return values


def _parse_day(value: str) -> tuple[int, str, date | None]:
    text = str(value).strip()
    if not text:
        raise AvailabilityInputError("proposed_day is required.")

    normalised = text.casefold().rstrip(".")
    if normalised in DAY_NAMES:
        weekday = DAY_NAMES[normalised]
        return weekday, (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )[weekday], None

    try:
        parsed_date = date.fromisoformat(text)
    except ValueError as exc:
        raise AvailabilityInputError(
            "proposed_day must be a weekday or an ISO date in YYYY-MM-DD format."
        ) from exc
    return parsed_date.weekday(), parsed_date.strftime("%A"), parsed_date


def _parse_time(value: Any, field_name: str) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)

    text = str(value).strip()
    if not text:
        raise AvailabilityInputError(f"{field_name} is required.")

    # get_schedule serialises Excel time values as ISO-compatible strings.
    for candidate in (text, text.upper().replace(".", "")):
        try:
            return time.fromisoformat(candidate).replace(second=0, microsecond=0)
        except ValueError:
            pass
        for pattern in ("%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(candidate, pattern).time()
            except ValueError:
                continue
    raise AvailabilityInputError(
        f"{field_name} must use a valid time such as 08:30, 14:15, or 2:15 PM."
    )


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _record_weekday(record: dict[str, Any]) -> int | None:
    day_value = _first_value(record, DAY_KEYS)
    if day_value not in (None, ""):
        key = str(day_value).strip().casefold().rstrip(".")
        if key in DAY_NAMES:
            return DAY_NAMES[key]

    date_value = _first_value(record, DATE_KEYS)
    if isinstance(date_value, (date, datetime)):
        return date_value.weekday()
    if date_value not in (None, ""):
        try:
            return date.fromisoformat(str(date_value).strip()[:10]).weekday()
        except ValueError:
            return None
    return None


def _record_date(record: dict[str, Any]) -> date | None:
    value = _first_value(record, DATE_KEYS)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value not in (None, ""):
        try:
            return date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            return None
    return None


def _parse_weeks(value: Any) -> set[int]:
    if value in (None, ""):
        return set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}

    weeks: set[int] = set()
    text = str(value).replace("–", "-").replace("—", "-")
    for start, end in re.findall(r"(\d+)\s*-\s*(\d+)", text):
        low, high = sorted((int(start), int(end)))
        weeks.update(range(low, high + 1))
    text_without_ranges = re.sub(r"\d+\s*-\s*\d+", " ", text)
    weeks.update(int(item) for item in re.findall(r"\d+", text_without_ranges))
    return weeks


def _teaching_weeks(context: list[str]) -> set[int]:
    weeks: set[int] = set()
    for line in context:
        match = re.search(
            r"(?:teaching|academic)\s+weeks?\s*[:=]?\s*(\d+)\s*[\-–—]\s*(\d+)",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            low, high = sorted((int(match.group(1)), int(match.group(2))))
            weeks.update(range(low, high + 1))
    return weeks


def _decode_tool_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "content"):
        raw_result = raw_result.content
    if isinstance(raw_result, dict):
        return raw_result
    if not isinstance(raw_result, str):
        raise ScheduleLookupError(
            "get_schedule returned an unsupported result type.",
            {"result_type": type(raw_result).__name__},
        )
    try:
        decoded = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        raise ScheduleLookupError(
            "get_schedule did not return structured JSON.",
            {"reason": str(exc)},
        ) from exc
    if not isinstance(decoded, dict):
        raise ScheduleLookupError("get_schedule returned JSON that is not an object.")
    return decoded


def _invoke_get_schedule(uploaded_file_path: str, **options: Any) -> dict[str, Any]:
    arguments = {"uploaded_file_path": uploaded_file_path}
    arguments.update({key: value for key, value in options.items() if value is not None})
    try:
        raw_result = get_schedule.invoke(arguments)
    except Exception as exc:
        raise ScheduleLookupError(
            "get_schedule could not be invoked.",
            {"exception_type": type(exc).__name__, "reason": str(exc)},
        ) from exc

    payload = _decode_tool_result(raw_result)
    if str(payload.get("status", "")).casefold() == "error":
        error = payload.get("error")
        details = error if isinstance(error, dict) else {"error": error}
        raise ScheduleLookupError(
            str(details.get("message") or "get_schedule could not load the schedule."),
            details,
        )
    return payload


def _extract_schedule_data(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records: list[dict[str, Any]] = []
    context: list[str] = []
    selected_sheets: list[str] = []

    extraction = payload.get("extraction")
    if isinstance(extraction, dict):
        selected = extraction.get("selected_sheets")
        if isinstance(selected, list):
            selected_sheets.extend(str(item) for item in selected)

        sheets = extraction.get("sheets")
        if isinstance(sheets, list):
            for sheet in sheets:
                if not isinstance(sheet, dict):
                    continue
                sheet_name = str(sheet.get("name") or "")
                for context_row in sheet.get("context", []):
                    if not isinstance(context_row, dict):
                        continue
                    values = context_row.get("values", [])
                    if isinstance(values, list):
                        context.extend(str(value) for value in values if value not in (None, ""))

                for table in sheet.get("tables", []):
                    if not isinstance(table, dict):
                        continue
                    for row in table.get("rows", []):
                        if not isinstance(row, dict):
                            continue
                        values = row.get("values")
                        if isinstance(values, dict):
                            records.append(
                                {
                                    **values,
                                    "_sheet": sheet_name,
                                    "_excel_row": row.get("excel_row"),
                                }
                            )

    # Also accept normalized schedule contracts from future get_schedule versions.
    for key in ("sessions", "events", "records", "items"):
        items = payload.get(key)
        if isinstance(items, list):
            records.extend(item for item in items if isinstance(item, dict))
    schedule = payload.get("schedule")
    if isinstance(schedule, list):
        records.extend(item for item in schedule if isinstance(item, dict))
    elif isinstance(schedule, dict):
        for key in ("sessions", "events", "records", "items"):
            items = schedule.get(key)
            if isinstance(items, list):
                records.extend(item for item in items if isinstance(item, dict))

    return records, context, selected_sheets


def _has_session_fields(record: dict[str, Any]) -> bool:
    keys = set(_normalised_record(record))
    return bool(keys & (DAY_KEYS | DATE_KEYS | START_KEYS | END_KEYS))


def _record_matches_staff(record: dict[str, Any], identifier: str) -> bool:
    requested = _normalise_identity(identifier)
    candidates = _values_for_keys(record, STAFF_ID_KEYS | STAFF_NAME_KEYS)
    return any(_normalise_identity(candidate) == requested for candidate in candidates)


def _directory_candidates(records: list[dict[str, Any]], identifier: str) -> list[str]:
    requested = _normalise_identity(identifier)
    candidate_ids: list[str] = []
    for record in records:
        identities = _values_for_keys(record, STAFF_ID_KEYS | STAFF_NAME_KEYS)
        if not any(_normalise_identity(value) == requested for value in identities):
            continue
        ids = _values_for_keys(record, STAFF_ID_KEYS)
        candidate_ids.extend(ids or [identifier])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_ids:
        key = _normalise_identity(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _load_staff_schedule(
    uploaded_file_path: str,
    identifier: str,
) -> tuple[list[dict[str, Any]], list[str], str]:
    direct_error: ScheduleLookupError | None = None
    try:
        direct_payload = _invoke_get_schedule(
            uploaded_file_path,
            sheet_name=identifier,
            max_rows=MAX_SCHEDULE_ROWS,
            max_chars=MAX_SCHEDULE_CHARS,
        )
        direct_records, direct_context, selected = _extract_schedule_data(direct_payload)
        sessions = [record for record in direct_records if _has_session_fields(record)]
        if sessions:
            resolved = selected[0] if selected else identifier
            return sessions, direct_context, resolved
    except ScheduleLookupError as exc:
        direct_error = exc

    # The identifier may be a name rather than a worksheet ID, or the uploaded
    # file may contain all staff in one combined timetable.
    try:
        query_payload = _invoke_get_schedule(
            uploaded_file_path,
            query=identifier,
            max_rows=MAX_SCHEDULE_ROWS,
            max_pages=50,
            max_chars=MAX_SCHEDULE_CHARS,
        )
    except ScheduleLookupError:
        if direct_error is not None:
            raise direct_error
        raise

    query_records, query_context, _ = _extract_schedule_data(query_payload)
    matching_sessions = [
        record
        for record in query_records
        if _has_session_fields(record) and _record_matches_staff(record, identifier)
    ]
    if matching_sessions:
        return matching_sessions, query_context, identifier

    candidates = _directory_candidates(query_records, identifier)
    if len(candidates) > 1:
        raise ScheduleLookupError(
            "The staff identifier is ambiguous.",
            {"requested_staff": identifier, "matching_staff_ids": candidates},
        )
    if len(candidates) == 1:
        resolved_id = candidates[0]
        detail_payload = _invoke_get_schedule(
            uploaded_file_path,
            sheet_name=resolved_id,
            max_rows=MAX_SCHEDULE_ROWS,
            max_chars=MAX_SCHEDULE_CHARS,
        )
        detail_records, detail_context, _ = _extract_schedule_data(detail_payload)
        sessions = [record for record in detail_records if _has_session_fields(record)]
        if sessions:
            return sessions, query_context + detail_context, resolved_id

    raise ScheduleLookupError(
        "The requested lecturer or teaching assistant could not be verified in the uploaded schedule.",
        {
            "requested_staff": identifier,
            "required_action": (
                "Provide the exact staff ID or full name and confirm that the uploaded file "
                "contains that staff member's complete timetable."
            ),
        },
    )


def _session_summary(record: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "course_id": {"courseid", "moduleid", "subjectid"},
        "course_name": {"coursename", "modulename", "subjectname", "title"},
        "section": {"section", "sectionid", "group"},
        "room": {"room", "roomid", "location"},
    }
    summary: dict[str, Any] = {
        "day": _first_value(record, DAY_KEYS) or _first_value(record, DATE_KEYS),
        "start": _first_value(record, START_KEYS),
        "end": _first_value(record, END_KEYS),
    }
    for output_key, keys in aliases.items():
        value = _first_value(record, keys)
        if value not in (None, ""):
            summary[output_key] = value
    if record.get("_sheet"):
        summary["source_sheet"] = record["_sheet"]
    if record.get("_excel_row"):
        summary["source_row"] = record["_excel_row"]
    return summary


def _evaluate_staff(
    identifier: str,
    resolved_identifier: str,
    records: list[dict[str, Any]],
    context: list[str],
    requested_weekday: int,
    requested_date: date | None,
    requested_start: time,
    requested_end: time,
    academic_week: int | None,
) -> dict[str, Any]:
    coverage = _teaching_weeks(context)
    if academic_week is not None and coverage and academic_week not in coverage:
        return {
            "staff_id": identifier,
            "resolved_staff_id": resolved_identifier,
            "availability": "unknown",
            "available": None,
            "conflicts": [],
            "reason": (
                f"The uploaded schedule covers teaching weeks {min(coverage)}-{max(coverage)}, "
                f"not academic week {academic_week}."
            ),
        }

    conflicts: list[dict[str, Any]] = []
    uncertainties: list[str] = []
    requested_start_minutes = _minutes(requested_start)
    requested_end_minutes = _minutes(requested_end)

    for record in records:
        status = _first_value(record, STATUS_KEYS)
        if status is not None:
            normalised_status = _normalise_identity(status)
            if normalised_status in CANCELLED_STATES | FREE_STATES:
                continue

        record_weekday = _record_weekday(record)
        has_day_data = _first_value(record, DAY_KEYS | DATE_KEYS) not in (None, "")
        if record_weekday is None:
            if has_day_data or _first_value(record, START_KEYS | END_KEYS) not in (None, ""):
                uncertainties.append("A session has an unreadable or missing day/date.")
            continue
        if record_weekday != requested_weekday:
            continue

        record_date = _record_date(record)
        if requested_date is not None and record_date is not None and record_date != requested_date:
            continue

        raw_start = _first_value(record, START_KEYS)
        raw_end = _first_value(record, END_KEYS)
        if raw_start in (None, "") or raw_end in (None, ""):
            uncertainties.append("A session on the requested day has a missing start or end time.")
            continue
        try:
            session_start = _parse_time(raw_start, "session start")
            session_end = _parse_time(raw_end, "session end")
        except AvailabilityInputError:
            uncertainties.append("A session on the requested day has an unreadable time.")
            continue

        session_start_minutes = _minutes(session_start)
        session_end_minutes = _minutes(session_end)
        if session_end_minutes <= session_start_minutes:
            uncertainties.append("A session on the requested day has an invalid time range.")
            continue

        weeks = _parse_weeks(_first_value(record, WEEK_KEYS))
        overlaps = (
            requested_start_minutes < session_end_minutes
            and requested_end_minutes > session_start_minutes
        )
        if not overlaps:
            continue
        if requested_date is None and record_date is not None:
            uncertainties.append(
                "A potentially conflicting session is date-specific; an exact proposed date is required."
            )
            continue
        if academic_week is not None and weeks and academic_week not in weeks:
            continue
        if academic_week is None and weeks:
            uncertainties.append(
                "A potentially conflicting session is week-specific; academic_week is required."
            )
            continue
        conflicts.append(_session_summary(record))

    if conflicts:
        return {
            "staff_id": identifier,
            "resolved_staff_id": resolved_identifier,
            "availability": "unavailable",
            "available": False,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "warnings": sorted(set(uncertainties)),
        }
    if uncertainties:
        return {
            "staff_id": identifier,
            "resolved_staff_id": resolved_identifier,
            "availability": "unknown",
            "available": None,
            "conflict_count": 0,
            "conflicts": [],
            "reason": "The schedule data is insufficient for a conflict-free confirmation.",
            "warnings": sorted(set(uncertainties)),
        }
    return {
        "staff_id": identifier,
        "resolved_staff_id": resolved_identifier,
        "availability": "available",
        "available": True,
        "conflict_count": 0,
        "conflicts": [],
    }


def _clean_staff_ids(staff_ids: list[str] | str) -> list[str]:
    if isinstance(staff_ids, str):
        staff_ids = [staff_ids]
    if not isinstance(staff_ids, list):
        raise AvailabilityInputError("staff_ids must be a list of staff IDs or full names.")

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in staff_ids:
        identifier = str(value).strip()
        key = _normalise_identity(identifier)
        if identifier and key not in seen:
            cleaned.append(identifier)
            seen.add(key)
    if not cleaned:
        raise AvailabilityInputError("At least one lecturer or teaching-assistant ID is required.")
    return cleaned


@tool
def check_lecturer_or_ta_availability(
    uploaded_file_path: str,
    staff_ids: list[str],
    proposed_day: str,
    proposed_start: str,
    proposed_end: str,
    academic_week: int | None = None,
) -> str:
    """Check whether lecturers or teaching assistants are free for a period.

    Load the uploaded staff schedule exclusively through ``get_schedule``. Staff
    can be supplied by exact ID or full name. A staff member is unavailable when
    a scheduled session overlaps any part of the proposed interval; immediately
    adjacent sessions do not conflict. The result is structured JSON and never
    treats missing, ambiguous, or malformed schedule data as availability.

    ``proposed_day`` accepts a weekday or an ISO ``YYYY-MM-DD`` date. Times may
    use 24-hour or AM/PM notation. Supply ``academic_week`` for week-specific
    schedules or exceptions.
    """
    request: dict[str, Any] = {
        "uploaded_file_path": uploaded_file_path,
        "staff_ids": staff_ids,
        "proposed_day": proposed_day,
        "proposed_start": proposed_start,
        "proposed_end": proposed_end,
        "academic_week": academic_week,
    }

    try:
        if not str(uploaded_file_path).strip():
            raise AvailabilityInputError("uploaded_file_path is required.")
        cleaned_ids = _clean_staff_ids(staff_ids)
        requested_weekday, resolved_day, requested_date = _parse_day(proposed_day)
        start_time = _parse_time(proposed_start, "proposed_start")
        end_time = _parse_time(proposed_end, "proposed_end")
        if _minutes(end_time) <= _minutes(start_time):
            raise AvailabilityInputError("proposed_end must be later than proposed_start.")
        if academic_week is not None and (not isinstance(academic_week, int) or academic_week < 1):
            raise AvailabilityInputError("academic_week must be a positive integer.")
    except AvailabilityInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "summary": str(exc),
                "request": request,
                "staff_results": [],
            }
        )

    results: list[dict[str, Any]] = []
    for identifier in cleaned_ids:
        try:
            records, context, resolved_identifier = _load_staff_schedule(
                str(uploaded_file_path).strip(), identifier
            )
            results.append(
                _evaluate_staff(
                    identifier=identifier,
                    resolved_identifier=resolved_identifier,
                    records=records,
                    context=context,
                    requested_weekday=requested_weekday,
                    requested_date=requested_date,
                    requested_start=start_time,
                    requested_end=end_time,
                    academic_week=academic_week,
                )
            )
        except ScheduleLookupError as exc:
            results.append(
                {
                    "staff_id": identifier,
                    "availability": "unknown",
                    "available": None,
                    "conflicts": [],
                    "reason": str(exc),
                    "details": exc.details,
                }
            )

    available_ids = [item["staff_id"] for item in results if item["availability"] == "available"]
    unavailable_ids = [
        item["staff_id"] for item in results if item["availability"] == "unavailable"
    ]
    unknown_ids = [item["staff_id"] for item in results if item["availability"] == "unknown"]

    if unavailable_ids:
        all_available: bool | None = False
    elif unknown_ids:
        all_available = None
    else:
        all_available = True

    if unknown_ids:
        status = "information_required"
        summary = (
            "Availability could not be confirmed for: " + ", ".join(unknown_ids) + "."
        )
    elif unavailable_ids:
        status = "success"
        summary = "Unavailable staff: " + ", ".join(unavailable_ids) + "."
    else:
        status = "success"
        summary = "All requested lecturers and teaching assistants are available."

    response: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "request": {
            "source_file": str(uploaded_file_path).strip(),
            "staff_ids": cleaned_ids,
            "day": proposed_day,
            "resolved_weekday": resolved_day,
            "start": start_time.strftime("%H:%M"),
            "end": end_time.strftime("%H:%M"),
            "academic_week": academic_week,
        },
        "all_available": all_available,
        "available_staff_ids": available_ids,
        "unavailable_staff_ids": unavailable_ids,
        "unknown_staff_ids": unknown_ids,
        "staff_results": results,
    }
    if unknown_ids:
        response["required_action"] = (
            "Provide the missing exact staff identifier, academic week, or a complete readable "
            "staff schedule before using this period in a repair."
        )
    return _json(response)
