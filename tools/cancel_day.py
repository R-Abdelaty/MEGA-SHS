"""Read-only full-day cancellation and compensation orchestrator."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from langchain.tools import tool

from .check_lecturer_or_ta_availability import check_lecturer_or_ta_availability
from .check_priority import check_priority
from .check_room_availability import check_room_availability
from .find_affected_sessions import find_affected_sessions
from .get_schedule import get_schedule
from .report_disruption import report_disruption
from .run_schedule_repair import run_schedule_repair


MAX_ROWS_PER_CALL = 500
MAX_CHARS_PER_CALL = 120_000
MAX_PAGES = 100
MAX_RESULT_LIMIT = 100
TEACHING_WEEK_MAX = 12
MAX_PROTOTYPE_CACHE_ENTRIES = 4
_PROTOTYPE_CACHE: dict[str, dict[str, Any]] = {}

DAY_ORDER = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}
DAY_NAMES = {name: name.title() for name in DAY_ORDER}
DAY_ALIASES = {
    "sun": "Sunday",
    "sunday": "Sunday",
    "mon": "Monday",
    "monday": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "tuesday": "Tuesday",
    "wed": "Wednesday",
    "wednesday": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "thursday": "Thursday",
    "fri": "Friday",
    "friday": "Friday",
    "sat": "Saturday",
    "saturday": "Saturday",
}
INACTIVE_STATUSES = {"cancelled", "canceled", "deleted", "inactive", "removed"}

ALIASES: dict[str, set[str]] = {
    "session_key": {
        "affectedsessionkey",
        "sessionkey",
        "sessionid",
        "eventid",
        "bookingid",
        "activityid",
        "examid",
        "assessmentid",
    },
    "course_id": {"courseid", "coursecode", "moduleid", "modulecode"},
    "course_name": {"coursename", "subject", "subjectname", "modulename"},
    "session_type": {"sessiontype", "activitytype", "assessmenttype", "type"},
    "day": {"day", "weekday", "dayofweek", "sessionday"},
    "period": {"period", "slot", "timeslot"},
    "period_id": {"periodid", "periodcode"},
    "start": {"start", "starttime", "from", "sessionstart", "examstart"},
    "end": {"end", "endtime", "to", "sessionend", "examend"},
    "week": {"week", "weeks", "weeknumber", "academicweek", "semesterweek"},
    "room": {"room", "roomid", "venue", "location", "hall"},
    "room_type": {"roomtype", "requiredroomtype", "venuetype"},
    "room_capacity": {"roomcapacity", "capacity", "seatcapacity"},
    "expected_students": {
        "expectedstudents",
        "studentcount",
        "students",
        "enrollment",
        "enrolment",
        "candidates",
    },
    "student_groups": {
        "studentgroups",
        "cohortgroups",
        "cohortgroup",
        "tutorialgroups",
        "groups",
    },
    "instructor": {
        "instructor",
        "instructorname",
        "lecturer",
        "doctor",
        "staff",
        "ta",
        "teachingassistant",
    },
    "major": {"major", "majors", "majorcode", "majorcodes"},
    "year": {"year", "academicyear", "studyyear"},
    "status": {"status", "sessionstatus", "bookingstatus"},
}


class CancellationInputError(ValueError):
    """Raised when the requested dry run is unsafe or incomplete."""


class CancellationDependencyError(RuntimeError):
    """Raised when a sibling tool cannot provide a complete result."""

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _identity(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _record_map(record: dict[str, Any]) -> dict[str, Any]:
    return {
        _normalise_key(key): value
        for key, value in record.items()
        if not str(key).startswith("_")
    }


def _value(record: dict[str, Any], field: str) -> Any:
    mapped = _record_map(record)
    for alias in ALIASES[field]:
        if mapped.get(alias) not in (None, ""):
            return mapped[alias]
    requirements = record.get("compensation_requirements")
    if isinstance(requirements, dict):
        requirement_fields = {
            "session_type": "session_type",
            "day": "original_day",
            "period": "original_period",
            "start": "original_start",
            "end": "original_end",
            "room": "original_room",
            "room_type": "required_room_type",
            "expected_students": "minimum_room_capacity",
            "student_groups": "student_groups",
        }
        key = requirement_fields.get(field)
        if key and requirements.get(key) not in (None, ""):
            return requirements[key]
        if field == "instructor":
            staff = requirements.get("required_staff")
            if isinstance(staff, dict):
                for key in ("instructor", "instructor_id", "teaching_assistant", "teaching_assistant_id"):
                    if staff.get(key) not in (None, ""):
                        return staff[key]
    return None


def _split(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[;,|\n]+", str(value))
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = " ".join(str(item).strip().split())
        key = _identity(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _parse_day(value: Any) -> str:
    text = str(value or "").strip()
    day = DAY_ALIASES.get(text.casefold().rstrip("."))
    if day:
        return day
    try:
        return date.fromisoformat(text[:10]).strftime("%A")
    except ValueError as exc:
        raise CancellationInputError(
            "day must be a weekday or ISO date in YYYY-MM-DD format."
        ) from exc


def _parse_time(value: Any, field: str) -> tuple[str, int]:
    text = str(value or "").strip().upper().replace(".", "")
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            parsed = datetime.strptime(text, pattern)
            minutes = parsed.hour * 60 + parsed.minute
            return f"{parsed.hour:02d}:{parsed.minute:02d}", minutes
        except ValueError:
            continue
    raise CancellationInputError(f"{field} contains an unreadable time: {value!r}.")


def _number(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _week(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _decode(raw: Any, tool_name: str) -> dict[str, Any]:
    if hasattr(raw, "content"):
        raw = raw.content
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CancellationDependencyError(
                f"{tool_name} returned invalid JSON.", str(exc)
            ) from exc
    if not isinstance(raw, dict):
        raise CancellationDependencyError(
            f"{tool_name} did not return a JSON object."
        )
    return raw


def _invoke(handle: Any, arguments: dict[str, Any], tool_name: str) -> dict[str, Any]:
    try:
        return _decode(handle.invoke(arguments), tool_name)
    except CancellationDependencyError:
        raise
    except Exception as exc:
        raise CancellationDependencyError(
            f"{tool_name} could not be invoked.",
            {"exception_type": type(exc).__name__, "reason": str(exc)},
        ) from exc


def _invoke_bulk_staff(arguments: dict[str, Any]) -> dict[str, Any]:
    """Call the staff tool directly when possible to avoid repeated schema copies."""
    function = getattr(check_lecturer_or_ta_availability, "func", None)
    if callable(function):
        try:
            return _decode(function(**arguments), "check_lecturer_or_ta_availability")
        except Exception as exc:
            raise CancellationDependencyError(
                "check_lecturer_or_ta_availability could not be invoked.",
                {"exception_type": type(exc).__name__, "reason": str(exc)},
            ) from exc
    return _invoke(
        check_lecturer_or_ta_availability,
        arguments,
        "check_lecturer_or_ta_availability",
    )


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    extraction = payload.get("extraction")
    if not isinstance(extraction, dict):
        return []
    rows: list[dict[str, Any]] = []
    for sheet in extraction.get("sheets") or []:
        if not isinstance(sheet, dict):
            continue
        for table in sheet.get("tables") or []:
            if not isinstance(table, dict):
                continue
            for row in table.get("rows") or []:
                if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                    continue
                rows.append(
                    {
                        **row["values"],
                        "_source_sheet": sheet.get("name"),
                        "_source_table": table.get("name"),
                        "_source_row": row.get("excel_row"),
                    }
                )
    return rows


def _retrieve_all(file_name: str, sheet_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    rows: list[dict[str, Any]] = []
    expected: int | None = None
    seen: set[tuple[Any, Any, Any]] = set()
    for page in range(1, MAX_PAGES + 1):
        payload = _invoke(
            get_schedule,
            {
                "uploaded_file_path": file_name,
                "sheet_name": sheet_name,
                "row_offset": offset,
                "max_rows": MAX_ROWS_PER_CALL,
                "max_chars": MAX_CHARS_PER_CALL,
            },
            "get_schedule",
        )
        if str(payload.get("status", "")).casefold() not in {"ok", "success"}:
            raise CancellationDependencyError(
                f"get_schedule could not read {file_name}::{sheet_name}.", payload
            )
        extraction = payload.get("extraction")
        limits = payload.get("limits")
        if not isinstance(extraction, dict) or not isinstance(limits, dict):
            raise CancellationDependencyError(
                "get_schedule omitted completeness metadata.", payload
            )
        found = extraction.get("matching_rows_found")
        if not isinstance(found, int):
            raise CancellationDependencyError(
                "get_schedule omitted matching_rows_found.", payload
            )
        if expected is None:
            expected = found
        elif found != expected:
            raise CancellationDependencyError(
                "A source schedule changed during pagination."
            )
        for row in _extract_rows(payload):
            identity = (
                row.get("_source_sheet"),
                row.get("_source_table"),
                row.get("_source_row"),
            )
            if identity not in seen:
                seen.add(identity)
                rows.append(row)
        if extraction.get("has_more") is False:
            if limits.get("truncated") is True or limits.get("completeness") != "complete":
                raise CancellationDependencyError(
                    "A final source page was incomplete.", limits
                )
            if len(rows) != expected:
                raise CancellationDependencyError(
                    "Retrieved row count does not match the reported total.",
                    {"expected": expected, "retrieved": len(rows)},
                )
            return rows, {
                "file": file_name,
                "sheet": sheet_name,
                "rows": len(rows),
                "pages": page,
                "complete": True,
            }
        next_offset = extraction.get("next_row_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise CancellationDependencyError(
                "get_schedule returned invalid pagination metadata.", payload
            )
        offset = next_offset
    raise CancellationDependencyError("Schedule retrieval exceeded the safe page limit.")


def _collect_affected(
    file_name: str,
    day: str,
    academic_week: int,
    sheet_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    sessions: list[dict[str, Any]] = []
    expected: int | None = None
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        payload = _invoke(
            find_affected_sessions,
            {
                "uploaded_file_path": file_name,
                "affected_day_or_date": day,
                "academic_week": academic_week,
                "sheet_name": sheet_name,
                "result_offset": offset,
                "result_limit": MAX_RESULT_LIMIT,
            },
            "find_affected_sessions",
        )
        if payload.get("status") != "success" or payload.get("complete") is not True:
            raise CancellationDependencyError(
                "find_affected_sessions did not return a complete page.", payload
            )
        total = payload.get("affected_session_count")
        if not isinstance(total, int):
            raise CancellationDependencyError(
                "find_affected_sessions omitted affected_session_count.", payload
            )
        if expected is None:
            expected = total
        elif total != expected:
            raise CancellationDependencyError(
                "The affected-session count changed during pagination."
            )
        for session in payload.get("affected_sessions") or []:
            key = str(_value(session, "session_key") or "").strip()
            if not key or _identity(key) in seen:
                raise CancellationDependencyError(
                    "Affected sessions require unique stable identifiers.", key
                )
            seen.add(_identity(key))
            sessions.append(session)
        pagination = payload.get("result_pagination")
        if not isinstance(pagination, dict):
            raise CancellationDependencyError(
                "find_affected_sessions omitted pagination metadata."
            )
        if pagination.get("has_more") is False:
            if len(sessions) != expected:
                raise CancellationDependencyError(
                    "Affected-session detail count does not match the total."
                )
            return sessions, {
                "affected_session_count": expected,
                "pages": page,
                "complete": True,
            }
        next_offset = pagination.get("next_result_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise CancellationDependencyError(
                "find_affected_sessions returned invalid result pagination."
            )
        offset = next_offset
    raise CancellationDependencyError("Affected-session retrieval exceeded the safe page limit.")


def _normalise_session(record: dict[str, Any]) -> dict[str, Any]:
    key = str(_value(record, "session_key") or "").strip()
    day = _parse_day(_value(record, "day"))
    start, start_minutes = _parse_time(_value(record, "start"), f"{key}.start")
    end, end_minutes = _parse_time(_value(record, "end"), f"{key}.end")
    if end_minutes <= start_minutes:
        raise CancellationInputError(f"Session {key!r} has an invalid time range.")
    expected = _number(_value(record, "expected_students"))
    if not key or expected is None:
        raise CancellationInputError(
            f"Session {key or '<unknown>'!r} lacks a stable ID or expected-student count."
        )
    groups = _split(_value(record, "student_groups"))
    staff = _split(_value(record, "instructor"))
    if not groups or not staff:
        raise CancellationInputError(
            f"Session {key!r} lacks student-group or staff information."
        )
    return {
        "session_key": key,
        "course_id": _value(record, "course_id"),
        "course_name": _value(record, "course_name"),
        "session_type": str(_value(record, "session_type") or "").strip(),
        "day": day,
        "period": _value(record, "period"),
        "period_id": _value(record, "period_id") or _value(record, "period"),
        "start": start,
        "end": end,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "duration": end_minutes - start_minutes,
        "room": str(_value(record, "room") or "").strip(),
        "room_type": str(_value(record, "room_type") or "").strip(),
        "expected_students": expected,
        "student_groups": groups,
        "staff": staff,
        "majors": _split(_value(record, "major")),
        "year": _number(_value(record, "year")),
        "raw": record,
    }


def _periods(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        try:
            start, start_minutes = _parse_time(_value(row, "start"), "schedule.start")
            end, end_minutes = _parse_time(_value(row, "end"), "schedule.end")
        except CancellationInputError:
            continue
        if end_minutes <= start_minutes:
            continue
        key = (start, end)
        unique.setdefault(
            key,
            {
                "period": _value(row, "period"),
                "period_id": _value(row, "period_id") or _value(row, "period"),
                "start": start,
                "end": end,
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "duration": end_minutes - start_minutes,
            },
        )
    result = sorted(unique.values(), key=lambda item: item["start_minutes"])
    if not result:
        raise CancellationInputError("No readable candidate periods were found.")
    return result


def _base_conflict_indexes(rows: list[dict[str, Any]]) -> tuple[dict, dict, dict, dict, dict]:
    groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    staff: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    rooms: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    group_days: dict[str, set[str]] = defaultdict(set)
    staff_days: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if _identity(_value(row, "status")) in INACTIVE_STATUSES:
            continue
        try:
            day = _parse_day(_value(row, "day"))
            start, _ = _parse_time(_value(row, "start"), "schedule.start")
            end, _ = _parse_time(_value(row, "end"), "schedule.end")
        except CancellationInputError:
            continue
        slot = (day, start, end)
        row_groups = {_identity(item) for item in _split(_value(row, "student_groups"))}
        row_staff = {_identity(item) for item in _split(_value(row, "instructor"))}
        room = _identity(_value(row, "room"))
        groups[slot].update(row_groups)
        staff[slot].update(row_staff)
        if room:
            rooms[slot].add(room)
        for item in row_groups:
            group_days[item].add(day)
        for item in row_staff:
            staff_days[item].add(day)
    return groups, staff, rooms, group_days, staff_days


def _exam_indexes(rows: list[dict[str, Any]]) -> tuple[dict, dict]:
    majors: dict[tuple[int, str, str, str], set[tuple[str, int | None]]] = defaultdict(set)
    groups: dict[tuple[int, str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        week = _week(_value(row, "week"))
        if week is None:
            continue
        try:
            day = _parse_day(_value(row, "day"))
            start, _ = _parse_time(_value(row, "start"), "exam.start")
            end, _ = _parse_time(_value(row, "end"), "exam.end")
        except CancellationInputError:
            continue
        key = (week, day, start, end)
        year = _number(_value(row, "year"))
        for major in _split(_value(row, "major")):
            majors[key].add((_identity(major), year))
        groups[key].update(_identity(item) for item in _split(_value(row, "student_groups")))
    return majors, groups


def _overlaps_exam(
    session: dict[str, Any],
    week: int,
    day: str,
    period: dict[str, Any],
    exam_majors: dict,
    exam_groups: dict,
) -> bool:
    key = (week, day, period["start"], period["end"])
    session_groups = {_identity(item) for item in session["student_groups"]}
    if session_groups & exam_groups.get(key, set()):
        return True
    session_majors = {_identity(item) for item in session["majors"]}
    for major, year in exam_majors.get(key, set()):
        if major in session_majors and (year is None or session["year"] is None or year == session["year"]):
            return True
    return False


def _prototype_id(disruption_id: str, assignments: list[dict[str, Any]]) -> str:
    signature = "|".join(
        f"{item['session_id']}:{item['week']}:{item['day']}:{item['start']}:{item['room']}"
        for item in assignments
    )
    digest = hashlib.sha256(f"{disruption_id}|{signature}".encode()).hexdigest()
    return "PRT-" + digest[:12].upper()


def _request_cache_key(request: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in request.items()
        if key not in {"result_offset", "result_limit"}
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _cached_page(
    cached: dict[str, Any], result_offset: int, result_limit: int
) -> dict[str, Any]:
    response = copy.deepcopy(cached["response"])
    rows = cached["rows"]
    if result_offset > len(rows):
        return {
            "status": "invalid_request",
            "prototype_complete": response.get("prototype_complete", False),
            "summary": "result_offset is beyond the prototype timetable.",
            "prototype_session_count": len(rows),
            "prototype_timetable": None,
            "source_files_modified": False,
        }
    page_end = min(result_offset + result_limit, len(rows))
    timetable = response["prototype_timetable"]
    timetable["sessions"] = rows[result_offset:page_end]
    timetable["returned_compensation_sessions"] = page_end - result_offset
    timetable["pagination"] = {
        "result_offset": result_offset,
        "result_limit": result_limit,
        "has_more": page_end < len(rows),
        "next_result_offset": page_end if page_end < len(rows) else None,
        "page_is_final": page_end >= len(rows),
    }
    response["orchestration"]["cache_hit"] = True
    return response


@tool
def cancel_day(
    day: str,
    academic_week: int,
    reason: str,
    cancellation_approved: bool,
    general_schedule_file: str = "05_General_Schedule.xlsx",
    staff_schedule_file: str = "Doctor Schedule Calendar.xlsx",
    room_schedule_file: str = "01_Room_Schedule.xlsx",
    exam_schedule_file: str = "06_Exam_Schedule.xlsx",
    sheet_name: str = "Semester Timetable",
    maximum_following_weeks: int = 2,
    result_offset: int = 0,
    result_limit: int = 50,
) -> str:
    """Create one read-only compensation timetable for a cancelled day.

    This is the parent orchestration tool for a confirmed whole-day disruption.
    It reports the disruption, retrieves every affected session, obtains the
    complete priority order, loads authoritative timetable and exam data, checks
    staff and room availability, chooses one conflict-screened assignment per
    session, and delegates the final in-memory transformation to
    ``run_schedule_repair``.

    Compensation is limited to the next one or two teaching weeks. Earlier weeks
    and earlier university days are preferred, while existing participant campus
    days are favored over creating a new day. The result is a prototype only: no
    uploaded workbook is edited, no cancellation is applied, and no repair is
    approved. Detailed timetable rows are paginated with ``result_offset`` and
    ``result_limit``.
    """
    request = {
        "day": day,
        "academic_week": academic_week,
        "reason": reason,
        "cancellation_approved": cancellation_approved,
        "general_schedule_file": general_schedule_file,
        "staff_schedule_file": staff_schedule_file,
        "room_schedule_file": room_schedule_file,
        "exam_schedule_file": exam_schedule_file,
        "sheet_name": sheet_name,
        "maximum_following_weeks": maximum_following_weeks,
        "result_offset": result_offset,
        "result_limit": result_limit,
    }
    try:
        resolved_day = _parse_day(day)
        if not isinstance(academic_week, int) or isinstance(academic_week, bool) or not 1 <= academic_week <= TEACHING_WEEK_MAX:
            raise CancellationInputError(
                f"academic_week must be between 1 and {TEACHING_WEEK_MAX}."
            )
        if not str(reason).strip():
            raise CancellationInputError("reason is required.")
        if cancellation_approved is not True:
            raise CancellationInputError(
                "The day cancellation must be explicitly confirmed before creating a prototype."
            )
        for field, value in (
            ("general_schedule_file", general_schedule_file),
            ("staff_schedule_file", staff_schedule_file),
            ("room_schedule_file", room_schedule_file),
            ("exam_schedule_file", exam_schedule_file),
            ("sheet_name", sheet_name),
        ):
            if not isinstance(value, str) or not value.strip():
                raise CancellationInputError(f"{field} is required.")
        if isinstance(maximum_following_weeks, bool) or maximum_following_weeks not in {1, 2}:
            raise CancellationInputError("maximum_following_weeks must be 1 or 2.")
        if isinstance(result_offset, bool) or not isinstance(result_offset, int) or result_offset < 0:
            raise CancellationInputError("result_offset must be a non-negative integer.")
        if isinstance(result_limit, bool) or not isinstance(result_limit, int) or not 1 <= result_limit <= MAX_RESULT_LIMIT:
            raise CancellationInputError(
                f"result_limit must be between 1 and {MAX_RESULT_LIMIT}."
            )
        target_weeks = [
            week
            for week in range(academic_week + 1, academic_week + maximum_following_weeks + 1)
            if week <= TEACHING_WEEK_MAX
        ]
        if not target_weeks:
            raise CancellationInputError(
                "No following teaching week is available before the finals blackout."
            )
    except CancellationInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "prototype_complete": False,
                "summary": str(exc),
                "request": request,
                "prototype_timetable": None,
                "source_files_modified": False,
            }
        )

    cache_key = _request_cache_key(request)
    if cache_key in _PROTOTYPE_CACHE:
        return _json(
            _cached_page(_PROTOTYPE_CACHE[cache_key], result_offset, result_limit)
        )

    try:
        report_payload = _invoke(
            report_disruption,
            {
                "disruption_type": "day_cancelled",
                "description": str(reason).strip(),
                "affected_day_or_date": resolved_day,
                "academic_week": academic_week,
                "whole_day": True,
            },
            "report_disruption",
        )
        if report_payload.get("status") != "success" or report_payload.get("report_complete") is not True:
            raise CancellationDependencyError(
                "report_disruption did not accept the confirmed cancellation.", report_payload
            )
        affected, affected_retrieval = _collect_affected(
            general_schedule_file.strip(), resolved_day, academic_week, sheet_name.strip()
        )
        priority_payload = _invoke(
            check_priority,
            {
                "uploaded_file_path": general_schedule_file.strip(),
                "affected_day_or_date": resolved_day,
                "academic_week": academic_week,
                "sheet_name": sheet_name.strip(),
                "disruption_details": str(reason).strip(),
                "result_offset": 0,
                "result_limit": MAX_RESULT_LIMIT,
            },
            "check_priority",
        )
        if priority_payload.get("status") != "success" or priority_payload.get("ranking_complete") is not True:
            raise CancellationDependencyError(
                "check_priority did not produce a complete global order.", priority_payload
            )
        priority_order = priority_payload.get("global_repair_order")
        if not isinstance(priority_order, list):
            raise CancellationDependencyError("check_priority omitted global_repair_order.")
        general_rows, general_retrieval = _retrieve_all(
            general_schedule_file.strip(), sheet_name.strip()
        )
        exam_rows, exam_retrieval = _retrieve_all(
            exam_schedule_file.strip(), "Regular Assessments"
        )
        doctor_rows, doctor_retrieval = _retrieve_all(
            staff_schedule_file.strip(), "Doctor Directory"
        )
    except CancellationDependencyError as exc:
        return _json(
            {
                "status": "information_required",
                "prototype_complete": False,
                "summary": str(exc),
                "details": exc.details,
                "request": request,
                "prototype_timetable": None,
                "source_files_modified": False,
                "required_action": "Correct the incomplete source or dependency result, then retry the dry run.",
            }
        )

    try:
        raw_by_key = {
            _identity(_value(record, "session_key")): record for record in affected
        }
        normalized_by_key = {
            key: _normalise_session(record) for key, record in raw_by_key.items()
        }
        normalized_order = [_identity(value) for value in priority_order]
        if set(normalized_order) != set(normalized_by_key):
            raise CancellationInputError(
                "The priority order does not exactly match the affected-session scope."
            )
        periods = _periods(general_rows)
        base_groups, base_staff, _base_rooms, group_days, staff_days = _base_conflict_indexes(general_rows)
        exam_majors, exam_groups = _exam_indexes(exam_rows)
        known_doctors = {
            _identity(_record_map(row).get("doctorname"))
            for row in doctor_rows
            if _record_map(row).get("doctorname") not in (None, "")
        }
    except CancellationInputError as exc:
        return _json(
            {
                "status": "information_required",
                "prototype_complete": False,
                "summary": str(exc),
                "request": request,
                "prototype_timetable": None,
                "source_files_modified": False,
            }
        )

    placed_groups: dict[tuple[int, str, str, str], set[str]] = defaultdict(set)
    placed_staff: dict[tuple[int, str, str, str], set[str]] = defaultdict(set)
    placed_rooms: dict[tuple[int, str, str, str], set[str]] = defaultdict(set)
    participant_daily_load: Counter[tuple[int, str, str]] = Counter()
    room_cache: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    staff_cache: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    assignments: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    rejection_totals: Counter[str] = Counter()
    ordered_days = [DAY_NAMES[key] for key in sorted(DAY_ORDER, key=DAY_ORDER.get) if DAY_ORDER[key] <= 4]

    for order, normalized_key in enumerate(normalized_order, start=1):
        session = normalized_by_key[normalized_key]
        session_groups = {_identity(value) for value in session["student_groups"]}
        session_staff = {_identity(value) for value in session["staff"]}
        candidates: list[tuple[float, int, str, dict[str, Any]]] = []
        for target_week in target_weeks:
            for target_day in ordered_days:
                new_day_count = sum(
                    target_day not in group_days.get(group, set()) for group in session_groups
                ) + sum(
                    target_day not in staff_days.get(member, set()) for member in session_staff
                )
                daily_load = sum(
                    participant_daily_load[(target_week, target_day, participant)]
                    for participant in session_groups | session_staff
                )
                for period_index, period in enumerate(periods):
                    if period["duration"] != session["duration"]:
                        continue
                    slot = (target_day, period["start"], period["end"])
                    placed_slot = (target_week, *slot)
                    if session_groups & base_groups.get(slot, set()):
                        rejection_totals["student_base_conflict"] += 1
                        continue
                    if session_staff & base_staff.get(slot, set()):
                        rejection_totals["staff_base_conflict"] += 1
                        continue
                    if session_groups & placed_groups.get(placed_slot, set()):
                        rejection_totals["student_compensation_conflict"] += 1
                        continue
                    if session_staff & placed_staff.get(placed_slot, set()):
                        rejection_totals["staff_compensation_conflict"] += 1
                        continue
                    if _overlaps_exam(
                        session, target_week, target_day, period, exam_majors, exam_groups
                    ):
                        rejection_totals["assessment_conflict"] += 1
                        continue
                    score = (
                        (target_week - academic_week - 1) * 10_000
                        + DAY_ORDER[target_day.casefold()] * 100
                        + new_day_count * 250
                        + daily_load * 20
                        + period_index * 5
                        + abs(period["start_minutes"] - session["start_minutes"]) / 30
                    )
                    candidates.append((score, target_week, target_day, period))
        candidates.sort(key=lambda item: (item[0], item[1], DAY_ORDER[item[2].casefold()], item[3]["start_minutes"]))

        chosen: dict[str, Any] | None = None
        local_rejections: Counter[str] = Counter()
        for _score, target_week, target_day, period in candidates:
            slot_key = (target_week, target_day, period["start"], period["end"])
            if slot_key not in room_cache:
                room_cache[slot_key] = _invoke(
                    check_room_availability,
                    {
                        "uploaded_file_path": room_schedule_file.strip(),
                        "requested_day_or_date": target_day,
                        "requested_start": period["start"],
                        "requested_end": period["end"],
                        "academic_week": target_week,
                        "minimum_capacity": 0,
                        "required_features": [],
                        "room_types": [],
                    },
                    "check_room_availability",
                )
            room_payload = room_cache[slot_key]
            if room_payload.get("status") != "success" or not isinstance(room_payload.get("available_rooms"), list):
                local_rejections["room_availability_unconfirmed"] += 1
                continue
            available_rooms = []
            required_type = _identity(session["room_type"])
            for room in room_payload["available_rooms"]:
                if not isinstance(room, dict) or not room.get("room"):
                    continue
                room_name = _identity(room["room"])
                if room_name in placed_rooms.get(slot_key, set()):
                    continue
                capacity = _number(room.get("capacity"))
                if capacity is None or capacity < session["expected_students"]:
                    continue
                if required_type and _identity(room.get("type")) != required_type:
                    continue
                available_rooms.append(room)
            available_rooms.sort(
                key=lambda room: (
                    _identity(room.get("room")) != _identity(session["room"]),
                    _number(room.get("capacity")) or 10**9,
                    _identity(room.get("room")),
                )
            )
            if not available_rooms:
                local_rejections["no_suitable_room"] += 1
                continue

            room = available_rooms[0]
            chosen = {
                "session_id": session["session_key"],
                "week": target_week,
                "day": target_day,
                "period": period.get("period"),
                "period_id": period.get("period_id"),
                "start": period["start"],
                "end": period["end"],
                "room": room["room"],
                "room_type": room.get("type"),
                "room_capacity": room.get("capacity"),
                "repair_order": order,
            }
            placed_groups[slot_key].update(session_groups)
            placed_staff[slot_key].update(session_staff)
            placed_rooms[slot_key].add(_identity(room["room"]))
            for participant in session_groups | session_staff:
                participant_daily_load[(target_week, target_day, participant)] += 1
            assignments.append(chosen)
            break

        if chosen is None:
            rejection_totals.update(local_rejections)
            unassigned.append(
                {
                    "session_key": session["session_key"],
                    "repair_order": order,
                    "session_type": session["session_type"],
                    "reason": "No conflict-screened staff-and-room placement was found in the permitted following weeks.",
                    "rejection_counts": dict(sorted(local_rejections.items())),
                }
            )

    assignments_by_slot: dict[tuple[int, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_slot[
            (
                assignment["week"],
                assignment["day"],
                assignment["start"],
                assignment["end"],
            )
        ].append(assignment)
    rejected_assignment_keys: set[str] = set()
    for slot_key, slot_assignments in assignments_by_slot.items():
        target_week, target_day, start, end = slot_key
        staff_ids = sorted(
            {
                member
                for assignment in slot_assignments
                for member in normalized_by_key[_identity(assignment["session_id"])]["staff"]
            },
            key=str.casefold,
        )
        payload = _invoke_bulk_staff(
            {
                "uploaded_file_path": general_schedule_file.strip(),
                "staff_ids": staff_ids,
                "proposed_day": target_day,
                "proposed_start": start,
                "proposed_end": end,
                "academic_week": target_week,
                "schedule_rows": general_rows,
            }
        )
        staff_cache[slot_key] = payload
        unavailable = {
            _identity(value)
            for value in [
                *(payload.get("unavailable_staff_ids") or []),
                *(payload.get("unknown_staff_ids") or []),
            ]
        }
        if payload.get("status") != "success" or payload.get("all_available") is not True:
            for assignment in slot_assignments:
                session = normalized_by_key[_identity(assignment["session_id"])]
                if not unavailable or unavailable & {_identity(value) for value in session["staff"]}:
                    rejected_assignment_keys.add(_identity(assignment["session_id"]))
                    unassigned.append(
                        {
                            "session_key": session["session_key"],
                            "repair_order": assignment["repair_order"],
                            "session_type": session["session_type"],
                            "reason": "The final bulk staff-availability check was not confirmed.",
                            "rejection_counts": {"staff_availability_unconfirmed": 1},
                        }
                    )
                    rejection_totals["staff_availability_unconfirmed"] += 1
    if rejected_assignment_keys:
        assignments = [
            assignment
            for assignment in assignments
            if _identity(assignment["session_id"]) not in rejected_assignment_keys
        ]

    assigned_keys = {_identity(item["session_id"]) for item in assignments}
    assigned_rows = [
        raw_by_key[key] for key in normalized_order if key in assigned_keys
    ]
    if assignments:
        repair_payload = _invoke(
            run_schedule_repair,
            {
                "disruption_report": report_payload,
                "schedule_rows": assigned_rows,
                "affected_session_keys": [
                    normalized_by_key[key]["session_key"]
                    for key in normalized_order
                    if key in assigned_keys
                ],
                "repair_assignments": assignments,
            },
            "run_schedule_repair",
        )
        if repair_payload.get("status") != "success":
            return _json(
                {
                    "status": "information_required",
                    "prototype_complete": False,
                    "summary": "run_schedule_repair rejected the exact in-memory assignments.",
                    "details": repair_payload,
                    "prototype_timetable": None,
                    "source_files_modified": False,
                }
            )

    timetable_rows: list[dict[str, Any]] = []
    for assignment in assignments:
        session = normalized_by_key[_identity(assignment["session_id"])]
        timetable_rows.append(
            {
                "session_id": session["session_key"],
                "course_id": session["course_id"],
                "course_name": session["course_name"],
                "session_type": session["session_type"],
                "academic_week": assignment["week"],
                "day": assignment["day"],
                "period": assignment["period"],
                "period_id": assignment["period_id"],
                "start": assignment["start"],
                "end": assignment["end"],
                "room": assignment["room"],
                "room_type": assignment["room_type"],
                "room_capacity": assignment["room_capacity"],
                "expected_students": session["expected_students"],
                "student_groups": session["student_groups"],
                "staff": session["staff"],
                "repair_order": assignment["repair_order"],
                "change_reason": f"Compensation for cancelled {resolved_day}, academic week {academic_week}",
            }
        )
    timetable_rows.sort(
        key=lambda item: (
            item["academic_week"],
            DAY_ORDER[item["day"].casefold()],
            item["start"],
            item["repair_order"],
        )
    )
    week_summary: dict[str, dict[str, Any]] = {}
    for week in target_weeks:
        rows = [item for item in timetable_rows if item["academic_week"] == week]
        week_summary[str(week)] = {
            "compensation_session_count": len(rows),
            "sessions_by_day": dict(sorted(Counter(item["day"] for item in rows).items(), key=lambda pair: DAY_ORDER[pair[0].casefold()])),
            "sessions_by_type": dict(sorted(Counter(item["session_type"] for item in rows).items())),
        }

    if result_offset > len(timetable_rows):
        return _json(
            {
                "status": "invalid_request",
                "prototype_complete": not unassigned,
                "summary": "result_offset is beyond the prototype timetable.",
                "prototype_session_count": len(timetable_rows),
                "prototype_timetable": None,
                "source_files_modified": False,
            }
        )
    page_end = min(result_offset + result_limit, len(timetable_rows))
    page_rows = timetable_rows[result_offset:page_end]
    has_more = page_end < len(timetable_rows)
    complete = len(assignments) == len(affected) and not unassigned
    status = "success" if complete else "information_required"
    disruption_id = report_payload["disruption_report"]["disruption_id"]
    response = {
            "status": status,
            "prototype_complete": complete,
            "summary": (
                f"One read-only prototype timetable assigned {len(assignments)} of "
                f"{len(affected)} cancelled session(s) across academic week(s) "
                f"{', '.join(map(str, target_weeks))}."
            ),
            "prototype_id": _prototype_id(disruption_id, assignments),
            "disruption_id": disruption_id,
            "cancelled_scope": {
                "day": resolved_day,
                "academic_week": academic_week,
                "reason": str(reason).strip(),
                "affected_session_count": len(affected),
            },
            "prototype_timetable": {
                "status": "pending_user_confirmation",
                "target_academic_weeks": target_weeks,
                "week_summary": week_summary,
                "total_compensation_sessions": len(timetable_rows),
                "returned_compensation_sessions": len(page_rows),
                "sessions": page_rows,
                "pagination": {
                    "result_offset": result_offset,
                    "result_limit": result_limit,
                    "has_more": has_more,
                    "next_result_offset": page_end if has_more else None,
                    "page_is_final": not has_more,
                },
            },
            "unassigned_session_count": len(unassigned),
            "unassigned_sessions": unassigned[:100],
            "constraint_summary": {
                "priority_order_applied": "Exam/Quiz > Lecture > Laboratory > Tutorial",
                "following_week_limit": maximum_following_weeks,
                "earlier_weeks_preferred": True,
                "earlier_weekdays_preferred": True,
                "existing_participant_days_preferred": True,
                "base_student_conflicts_allowed": False,
                "base_staff_conflicts_allowed": False,
                "compensation_conflicts_allowed": False,
                "assessment_conflicts_allowed": False,
                "room_capacity_and_type_preserved": True,
                "rejection_counts": dict(sorted(rejection_totals.items())),
            },
            "orchestration": {
                "tools_used": [
                    "report_disruption",
                    "find_affected_sessions",
                    "check_priority",
                    "get_schedule",
                    "check_lecturer_or_ta_availability",
                    "check_room_availability",
                    "run_schedule_repair",
                ],
                "affected_retrieval": affected_retrieval,
                "general_schedule_retrieval": general_retrieval,
                "exam_schedule_retrieval": exam_retrieval,
                "doctor_directory_retrieval": doctor_retrieval,
                "known_doctor_count": len(known_doctors),
                "staff_availability_checks": len(staff_cache),
                "room_availability_checks": len(room_cache),
                "cache_hit": False,
            },
            "source_files_modified": False,
            "approval": {
                "status": "not_requested",
                "prototype_only": True,
                "required_next_action": (
                    "Review every prototype page. If accepted, materialize a separate candidate workbook, "
                    "compare it with the original, run check_validity, and request explicit approval."
                ),
            },
            "required_action": (
                "Review the unassigned sessions and constraints before confirmation."
                if not complete
                else "Review all prototype timetable pages before deciding whether to confirm this proposal."
            ),
        }
    if len(_PROTOTYPE_CACHE) >= MAX_PROTOTYPE_CACHE_ENTRIES:
        _PROTOTYPE_CACHE.pop(next(iter(_PROTOTYPE_CACHE)))
    _PROTOTYPE_CACHE[cache_key] = {
        "response": copy.deepcopy(response),
        "rows": copy.deepcopy(timetable_rows),
    }
    return _json(response)
