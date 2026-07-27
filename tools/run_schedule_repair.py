"""Deterministic, constraint-aware university schedule repair candidate engine.

The tool proposes in-memory compensation placements.  It never edits the
authoritative timetable, silently relaxes a hard constraint, validates an
unwritten workbook, or approves a repair.  Every returned option is explicitly
blocked from approval until it is materialized and passes ``check_validity``.
"""

from __future__ import annotations

import hashlib
import json
import math
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


MAX_ROWS_PER_SCHEDULE_CALL = 500
MAX_CHARS_PER_SCHEDULE_CALL = 120_000
MAX_RETRIEVAL_PAGES = 100
FIND_PAGE_SIZE = 100
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 100

DAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
DAY_ALIASES = {
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
    "sun": "Sunday",
    "sunday": "Sunday",
}

FIELD_ALIASES: dict[str, set[str]] = {
    "session_key": {
        "affectedsessionkey",
        "sessionid",
        "eventid",
        "bookingid",
        "activityid",
        "examid",
        "assessmentid",
    },
    "course_id": {"courseid", "coursecode", "moduleid", "modulecode"},
    "course_name": {"coursename", "subject", "subjectname", "modulename"},
    "session_type": {
        "sessiontype",
        "activitytype",
        "assessmenttype",
        "examtype",
        "classtype",
        "type",
    },
    "day": {"day", "weekday", "dayofweek", "sessionday"},
    "date": {"date", "sessiondate", "scheduleddate", "examdate"},
    "period": {"period", "periodid", "slot", "timeslot"},
    "start": {"start", "starttime", "from", "sessionstart", "examstart"},
    "end": {"end", "endtime", "to", "sessionend", "examend"},
    "week": {
        "week",
        "weeks",
        "weeknumber",
        "academicweek",
        "semesterweek",
        "teachingweek",
    },
    "status": {"status", "sessionstatus", "bookingstatus", "examstatus"},
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
        "minimumroomcapacity",
    },
    "student_groups": {
        "studentgroups",
        "cohortgroups",
        "cohortgroup",
        "tutorialgroups",
        "tutorialgroup",
        "groupid",
    },
    "instructor": {
        "instructor",
        "instructorname",
        "lecturer",
        "doctor",
        "staff",
    },
    "instructor_id": {
        "instructorid",
        "lecturerid",
        "doctorid",
        "staffid",
    },
    "teaching_assistant": {
        "ta",
        "taname",
        "teachingassistant",
        "teachingassistantname",
    },
    "teaching_assistant_id": {"taid", "teachingassistantid"},
    "equipment": {
        "equipment",
        "requiredequipment",
        "equipmentrequirements",
        "features",
    },
    "accessibility": {
        "accessibility",
        "accessibilityrequirements",
        "specialrequirements",
    },
    "duration_minutes": {"durationminutes"},
    "duration_periods": {"durationperiods", "numberofperiods"},
}

INACTIVE_STATUSES = {
    "cancelled",
    "canceled",
    "deleted",
    "inactive",
    "postponed",
    "removed",
}

PRIORITY_TIER_BY_TYPE = {
    "exam": 4,
    "final exam": 4,
    "midterm": 4,
    "quiz": 4,
    "assessment": 4,
    "lecture": 3,
    "laboratory": 2,
    "lab": 2,
    "workshop": 2,
    "tutorial": 1,
}


class RepairInputError(ValueError):
    """Raised when repair inputs are incomplete, contradictory, or unsafe."""


class RepairDependencyError(RuntimeError):
    """Raised when an existing tool cannot provide a complete reliable result."""

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalise_identity(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _split_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        values = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = re.split(r"[;,|\n]+", str(value))

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = " ".join(str(item).strip().split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _record_map(record: dict[str, Any]) -> dict[str, Any]:
    return {
        _normalise_key(key): value
        for key, value in record.items()
        if not str(key).startswith("_")
    }


def _value(record: dict[str, Any], field: str) -> Any:
    mapped = _record_map(record)
    for alias in FIELD_ALIASES[field]:
        value = mapped.get(alias)
        if value not in (None, ""):
            return value

    requirements = record.get("compensation_requirements")
    if isinstance(requirements, dict):
        requirement_aliases = {
            "day": "original_day",
            "date": "original_date",
            "period": "original_period",
            "start": "original_start",
            "end": "original_end",
            "room": "original_room",
            "room_type": "required_room_type",
            "equipment": "required_equipment",
            "accessibility": "accessibility_requirements",
            "expected_students": "minimum_room_capacity",
            "duration_minutes": "duration_minutes",
            "session_type": "session_type",
            "student_groups": "student_groups",
        }
        source_key = requirement_aliases.get(field)
        if source_key and requirements.get(source_key) not in (None, ""):
            return requirements[source_key]
        if field in {
            "instructor",
            "instructor_id",
            "teaching_assistant",
            "teaching_assistant_id",
        }:
            staff = requirements.get("required_staff")
            if isinstance(staff, dict) and staff.get(field) not in (None, ""):
                return staff[field]
    return None


def _parse_time(value: Any, field: str) -> tuple[str, int]:
    text = str(value or "").strip().upper().replace(".", "")
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            parsed = datetime.strptime(text, pattern)
            minutes = parsed.hour * 60 + parsed.minute
            return f"{parsed.hour:02d}:{parsed.minute:02d}", minutes
        except ValueError:
            continue
    raise RepairInputError(f"{field} must be a readable time such as 08:30 or 14:15.")


def _parse_day(value: Any, field: str = "day") -> str:
    text = str(value or "").strip()
    day = DAY_ALIASES.get(text.casefold().rstrip("."))
    if day:
        return day
    try:
        return date.fromisoformat(text[:10]).strftime("%A")
    except ValueError as exc:
        raise RepairInputError(
            f"{field} must be a weekday or ISO date in YYYY-MM-DD format."
        ) from exc


def _parse_weeks(value: Any) -> set[int] | None:
    """Return None for every week, a set for explicit weeks, or empty for unreadable."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}
    text = str(value).strip().casefold().replace("–", "-").replace("—", "-")
    if text in {"all", "all weeks", "every week", "weekly", "recurring"}:
        return None
    result: set[int] = set()
    for start, end in re.findall(r"(\d+)\s*-\s*(\d+)", text):
        low, high = sorted((int(start), int(end)))
        result.update(range(low, high + 1))
    remainder = re.sub(r"\d+\s*-\s*\d+", " ", text)
    result.update(int(item) for item in re.findall(r"\d+", remainder))
    return result


def _number(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _decode_tool_result(raw_result: Any, tool_name: str) -> dict[str, Any]:
    if hasattr(raw_result, "content"):
        raw_result = raw_result.content
    if isinstance(raw_result, dict):
        payload = raw_result
    elif isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise RepairDependencyError(
                f"{tool_name} returned invalid JSON.", str(exc)
            ) from exc
    else:
        raise RepairDependencyError(
            f"{tool_name} returned an unsupported result type.",
            type(raw_result).__name__,
        )
    if not isinstance(payload, dict):
        raise RepairDependencyError(f"{tool_name} did not return a JSON object.")
    return payload


def _invoke(handle: Any, arguments: dict[str, Any], tool_name: str) -> dict[str, Any]:
    try:
        raw = handle.invoke(arguments)
    except Exception as exc:
        raise RepairDependencyError(
            f"{tool_name} could not be invoked.",
            {"exception_type": type(exc).__name__, "reason": str(exc)},
        ) from exc
    return _decode_tool_result(raw, tool_name)


def _extract_schedule_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
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
                values = dict(row["values"])
                values["_source_sheet"] = sheet.get("name")
                values["_source_table"] = table.get("name")
                values["_source_row"] = row.get("excel_row")
                rows.append(values)
    return rows


def _retrieve_complete_schedule(file_name: str, sheet_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    rows: list[dict[str, Any]] = []
    expected_total: int | None = None
    seen_sources: set[tuple[Any, Any, Any]] = set()

    for page_number in range(1, MAX_RETRIEVAL_PAGES + 1):
        payload = _invoke(
            get_schedule,
            {
                "uploaded_file_path": file_name,
                "sheet_name": sheet_name,
                "row_offset": offset,
                "max_rows": MAX_ROWS_PER_SCHEDULE_CALL,
                "max_chars": MAX_CHARS_PER_SCHEDULE_CALL,
            },
            "get_schedule",
        )
        if str(payload.get("status", "")).casefold() not in {"ok", "success"}:
            raise RepairDependencyError("get_schedule did not complete successfully.", payload)
        extraction = payload.get("extraction")
        if not isinstance(extraction, dict):
            raise RepairDependencyError("get_schedule omitted extraction metadata.", payload)
        found = extraction.get("matching_rows_found")
        if not isinstance(found, int):
            raise RepairDependencyError("get_schedule omitted matching_rows_found.", payload)
        if expected_total is None:
            expected_total = found
        elif found != expected_total:
            raise RepairDependencyError("The schedule changed during paginated retrieval.")

        page_rows = _extract_schedule_rows(payload)
        for row in page_rows:
            source = (
                row.get("_source_sheet"),
                row.get("_source_table"),
                row.get("_source_row"),
            )
            if source in seen_sources:
                raise RepairDependencyError("get_schedule returned a duplicate source row.", source)
            seen_sources.add(source)
            rows.append(row)

        has_more = extraction.get("has_more")
        if has_more is False:
            if expected_total != len(rows):
                raise RepairDependencyError(
                    "Complete schedule retrieval count does not match the reported total.",
                    {"expected": expected_total, "retrieved": len(rows)},
                )
            return rows, {
                "file": file_name,
                "sheet": sheet_name,
                "matching_rows": expected_total,
                "pages_retrieved": page_number,
                "complete": True,
            }
        next_offset = extraction.get("next_row_offset")
        if has_more is not True or not isinstance(next_offset, int) or next_offset <= offset:
            raise RepairDependencyError("get_schedule returned invalid pagination metadata.", payload)
        offset = next_offset
    raise RepairDependencyError(
        f"Schedule retrieval exceeded {MAX_RETRIEVAL_PAGES} pages."
    )


def _collect_cancelled_day_sessions(
    file_name: str,
    sheet_name: str,
    affected_day_or_date: str,
    academic_week: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_total: int | None = None

    for page_number in range(1, MAX_RETRIEVAL_PAGES + 1):
        payload = _invoke(
            find_affected_sessions,
            {
                "uploaded_file_path": file_name,
                "affected_day_or_date": affected_day_or_date,
                "academic_week": academic_week,
                "sheet_name": sheet_name,
                "result_offset": offset,
                "result_limit": FIND_PAGE_SIZE,
            },
            "find_affected_sessions",
        )
        if payload.get("status") != "success" or payload.get("complete") is not True:
            raise RepairDependencyError(
                "find_affected_sessions did not return a complete verified page.", payload
            )
        total = payload.get("affected_session_count")
        if not isinstance(total, int):
            raise RepairDependencyError("find_affected_sessions omitted the total count.")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise RepairDependencyError("The affected-session scope changed during retrieval.")
        page_sessions = payload.get("affected_sessions")
        if not isinstance(page_sessions, list):
            raise RepairDependencyError("find_affected_sessions omitted detailed sessions.")
        for session in page_sessions:
            if not isinstance(session, dict):
                raise RepairDependencyError("An affected-session record is not an object.")
            key = str(_value(session, "session_key") or "").strip()
            if not key or key.casefold() in seen:
                raise RepairDependencyError(
                    "Affected sessions require unique stable identifiers.", key or None
                )
            seen.add(key.casefold())
            sessions.append(session)

        pagination = payload.get("result_pagination")
        if not isinstance(pagination, dict):
            raise RepairDependencyError("find_affected_sessions omitted pagination metadata.")
        if pagination.get("has_more") is False:
            if len(sessions) != expected_total:
                raise RepairDependencyError(
                    "Affected-session count does not match the complete detailed scope.",
                    {"expected": expected_total, "retrieved": len(sessions)},
                )
            return sessions, {
                "affected_session_count": expected_total,
                "pages_retrieved": page_number,
                "complete": True,
            }
        next_offset = pagination.get("next_result_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise RepairDependencyError("find_affected_sessions returned invalid pagination.")
        offset = next_offset
    raise RepairDependencyError(
        f"Affected-session retrieval exceeded {MAX_RETRIEVAL_PAGES} pages."
    )


def _collect_priority_order(
    file_name: str,
    sheet_name: str,
    affected_day_or_date: str,
    academic_week: int,
    expected_keys: set[str],
) -> tuple[list[str], dict[str, Any]]:
    payload = _invoke(
        check_priority,
        {
            "uploaded_file_path": file_name,
            "affected_day_or_date": affected_day_or_date,
            "academic_week": academic_week,
            "sheet_name": sheet_name,
            "result_offset": 0,
            "result_limit": 1,
        },
        "check_priority",
    )
    if payload.get("status") != "success" or payload.get("ranking_complete") is not True:
        raise RepairDependencyError("check_priority did not produce a complete ranking.", payload)
    order = payload.get("global_repair_order")
    if not isinstance(order, list) or any(not str(item).strip() for item in order):
        raise RepairDependencyError("check_priority omitted the global repair order.")
    cleaned = [str(item).strip() for item in order]
    normalized = {_normalise_identity(item) for item in cleaned}
    if len(cleaned) != len(normalized) or normalized != expected_keys:
        raise RepairDependencyError(
            "Priority order does not match the complete affected-session scope.",
            {
                "ranked_count": len(cleaned),
                "affected_count": len(expected_keys),
            },
        )
    return cleaned, {
        "ranked_session_count": len(cleaned),
        "ranking_complete": True,
        "policy": payload.get("policy"),
    }


def _unwrap_report(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepairInputError("disruption_report must be a JSON object.")
    if "disruption_report" in value:
        if value.get("status") != "success" or value.get("report_complete") is not True:
            raise RepairInputError(
                "The supplied report_disruption result is incomplete or unsuccessful."
            )
        report = value.get("disruption_report")
    else:
        report = value
    if not isinstance(report, dict):
        raise RepairInputError("disruption_report does not contain a report object.")
    required = ("disruption_id", "disruption_type", "scope")
    missing = [field for field in required if report.get(field) in (None, "")]
    if missing:
        raise RepairInputError(
            "disruption_report is missing normalized fields: " + ", ".join(missing) + "."
        )
    if not isinstance(report.get("scope"), dict):
        raise RepairInputError("disruption_report.scope must be an object.")
    return report


def _clean_priority_order(value: list[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RepairInputError("priority_order must be a list of session identifiers.")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        key = str(item).strip()
        normalized = _normalise_identity(key)
        if not key:
            raise RepairInputError(f"priority_order[{index}] is empty.")
        if normalized in seen:
            raise RepairInputError(f"priority_order contains duplicate session {key}.")
        seen.add(normalized)
        result.append(key)
    return result


def _priority_tier(session_type: Any) -> int | None:
    text = _normalise_identity(session_type)
    for label, tier in PRIORITY_TIER_BY_TYPE.items():
        if label in text:
            return tier
    return None


def _normalize_session(
    raw: dict[str, Any],
    repair_order: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    key = str(_value(raw, "session_key") or "").strip()
    session_type = _value(raw, "session_type")
    day_value = _value(raw, "day") or _value(raw, "date")
    errors: list[str] = []
    if not key:
        errors.append("stable session identifier")
    try:
        day = _parse_day(day_value, "session day")
    except RepairInputError:
        day = None
        errors.append("readable original day/date")
    try:
        start, start_minutes = _parse_time(_value(raw, "start"), "session start")
        end, end_minutes = _parse_time(_value(raw, "end"), "session end")
        if end_minutes <= start_minutes:
            raise RepairInputError("session end must be later than its start")
    except RepairInputError:
        start = end = None
        start_minutes = end_minutes = None
        errors.append("valid original start/end time")

    duration = _number(_value(raw, "duration_minutes"))
    if duration is None and start_minutes is not None and end_minutes is not None:
        duration = end_minutes - start_minutes
    if not duration:
        errors.append("positive session duration")

    staff: list[str] = []
    for field in (
        "instructor_id",
        "teaching_assistant_id",
        "instructor",
        "teaching_assistant",
    ):
        for identifier in _split_values(_value(raw, field)):
            if _normalise_identity(identifier) not in {
                _normalise_identity(item) for item in staff
            }:
                staff.append(identifier)
    groups = _split_values(_value(raw, "student_groups"))
    if not staff:
        errors.append("lecturer or teaching-assistant identifier")
    if not groups:
        errors.append("student group identifiers")

    expected_students = _number(_value(raw, "expected_students"))
    if expected_students is None:
        errors.append("expected student count/minimum room capacity")
    tier = _priority_tier(session_type)
    if tier is None:
        errors.append("confirmed exam/quiz, lecture, laboratory, or tutorial type")

    if errors:
        return None, errors
    assert day and start and end and start_minutes is not None and end_minutes is not None
    assert duration and expected_students is not None and tier is not None
    return {
        "session_key": key,
        "repair_order": repair_order,
        "priority_tier": tier,
        "course_id": _value(raw, "course_id"),
        "course_name": _value(raw, "course_name"),
        "session_type": session_type,
        "original": {
            "day": day,
            "date": _value(raw, "date"),
            "period": _value(raw, "period"),
            "start": start,
            "end": end,
            "start_minutes": start_minutes,
            "end_minutes": end_minutes,
            "room": _value(raw, "room"),
        },
        "duration_minutes": duration,
        "staff_ids": staff,
        "student_groups": groups,
        "expected_students": expected_students,
        "required_room_types": _split_values(_value(raw, "room_type")),
        "required_features": _split_values(_value(raw, "equipment"))
        + _split_values(_value(raw, "accessibility")),
        "source": raw.get("source") or raw.get("original_source"),
    }, []


def _normalize_slot(raw: dict[str, Any], source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RepairInputError(f"{source} must be an object.")
    day = _parse_day(raw.get("day") or raw.get("date"), f"{source}.day")
    start, start_minutes = _parse_time(raw.get("start"), f"{source}.start")
    end, end_minutes = _parse_time(raw.get("end"), f"{source}.end")
    if end_minutes <= start_minutes:
        raise RepairInputError(f"{source}.end must be later than its start.")
    return {
        "day": day,
        "date": raw.get("date"),
        "period": raw.get("period") or raw.get("period_id"),
        "start": start,
        "end": end,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "duration_minutes": end_minutes - start_minutes,
        "confirmed_nonstandard": raw.get("confirmed_nonstandard") is True,
        "source": source,
    }


def _derive_candidate_slots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            slot = _normalize_slot(
                {
                    "day": _value(row, "day") or _value(row, "date"),
                    "start": _value(row, "start"),
                    "end": _value(row, "end"),
                    "period": _value(row, "period"),
                },
                "authoritative_schedule",
            )
        except RepairInputError:
            continue
        key = (slot["day"], slot["start_minutes"], slot["end_minutes"])
        slots.setdefault(key, slot)
    return sorted(
        slots.values(),
        key=lambda item: (
            DAY_INDEX[item["day"].casefold()],
            item["start_minutes"],
            item["end_minutes"],
        ),
    )


def _normalize_candidate_slots(
    supplied: list[dict[str, Any]] | None,
    schedule_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if supplied is None:
        slots = _derive_candidate_slots(schedule_rows)
        source = "derived_from_authoritative_general_schedule"
    else:
        if not isinstance(supplied, list):
            raise RepairInputError("candidate_slots must be a list of time-window objects.")
        slots = [
            _normalize_slot(item, f"candidate_slots[{index}]")
            for index, item in enumerate(supplied)
        ]
        observed_slots = _derive_candidate_slots(schedule_rows)
        observed_times = {
            (item["start_minutes"], item["end_minutes"])
            for item in observed_slots
        }
        unconfirmed_nonstandard = [
            {
                "index": index,
                "day": item["day"],
                "start": item["start"],
                "end": item["end"],
            }
            for index, item in enumerate(slots)
            if (item["start_minutes"], item["end_minutes"]) not in observed_times
            and not item["confirmed_nonstandard"]
        ]
        if unconfirmed_nonstandard:
            raise RepairInputError(
                "Candidate periods outside the observed university period grid must set "
                "confirmed_nonstandard=true after explicit user confirmation: "
                + json.dumps(unconfirmed_nonstandard, ensure_ascii=False)
            )
        source = "supplied_by_ui"
    unique: dict[tuple[str, int, int], dict[str, Any]] = {}
    for slot in slots:
        unique.setdefault(
            (slot["day"], slot["start_minutes"], slot["end_minutes"]), slot
        )
    result = sorted(
        unique.values(),
        key=lambda item: (
            DAY_INDEX[item["day"].casefold()],
            item["start_minutes"],
            item["end_minutes"],
        ),
    )
    if not result:
        raise RepairInputError(
            "No candidate time periods were supplied or discoverable from the timetable."
        )
    return result, source


def _normalize_frozen_row(row: dict[str, Any], academic_week: int) -> tuple[dict[str, Any] | None, str | None]:
    status = _normalise_identity(_value(row, "status"))
    if status in INACTIVE_STATUSES:
        return None, None
    weeks = _parse_weeks(_value(row, "week"))
    if weeks == set():
        return None, "unreadable academic-week value"
    if weeks is not None and academic_week not in weeks:
        return None, None
    try:
        day = _parse_day(_value(row, "day") or _value(row, "date"), "schedule day")
        start, start_minutes = _parse_time(_value(row, "start"), "schedule start")
        end, end_minutes = _parse_time(_value(row, "end"), "schedule end")
        if end_minutes <= start_minutes:
            raise RepairInputError("invalid schedule interval")
    except RepairInputError as exc:
        return None, str(exc)
    staff_ids = _split_values(_value(row, "instructor_id"))
    staff_ids += _split_values(_value(row, "teaching_assistant_id"))
    staff_ids += _split_values(_value(row, "instructor"))
    staff_ids += _split_values(_value(row, "teaching_assistant"))
    student_groups = _split_values(_value(row, "student_groups"))
    session_tier = _priority_tier(_value(row, "session_type"))
    if session_tier is not None and not student_groups:
        return None, "academic session has no readable student-group identifiers"
    return {
        "session_key": str(_value(row, "session_key") or "").strip(),
        "day": day,
        "start": start,
        "end": end,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "room": str(_value(row, "room") or "").strip(),
        "staff_ids": staff_ids,
        "student_groups": student_groups,
        "source": {
            "sheet": row.get("_source_sheet"),
            "table": row.get("_source_table"),
            "row": row.get("_source_row"),
        },
    }, None


def _overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["day"] == second["day"]
        and first["start_minutes"] < second["end_minutes"]
        and first["end_minutes"] > second["start_minutes"]
    )


def _identity_set(values: list[str]) -> set[str]:
    return {_normalise_identity(value) for value in values if str(value).strip()}


def _local_conflict(
    session: dict[str, Any],
    slot: dict[str, Any],
    frozen: list[dict[str, Any]],
) -> dict[str, Any] | None:
    groups = _identity_set(session["student_groups"])
    staff = _identity_set(session["staff_ids"])
    for existing in frozen:
        if not _overlap(slot, existing):
            continue
        shared_groups = sorted(groups & _identity_set(existing["student_groups"]))
        if shared_groups:
            return {
                "code": "student_group_conflict",
                "conflicting_session": existing["session_key"],
                "shared_student_groups": shared_groups,
                "source": existing["source"],
            }
        shared_staff = sorted(staff & _identity_set(existing["staff_ids"]))
        if shared_staff:
            return {
                "code": "general_schedule_staff_conflict",
                "conflicting_session": existing["session_key"],
                "shared_staff": shared_staff,
                "source": existing["source"],
            }
    return None


def _reported_scope(report: dict[str, Any]) -> tuple[str | None, int | None, int | None, bool]:
    scope = report["scope"]
    day_value = scope.get("affected_date") or scope.get("affected_day")
    day = _parse_day(day_value, "disruption scope day") if day_value else None
    start = end = None
    if scope.get("start_time") and scope.get("end_time"):
        _, start = _parse_time(scope["start_time"], "disruption start_time")
        _, end = _parse_time(scope["end_time"], "disruption end_time")
    return day, start, end, bool(scope.get("whole_day"))


def _slot_inside_disruption(
    report: dict[str, Any],
    session: dict[str, Any],
    slot: dict[str, Any],
) -> bool:
    disruption_type = str(report["disruption_type"])
    day, start, end, whole_day = _reported_scope(report)
    if disruption_type in {"room_closed", "room_capacity_corrected", "university_event"}:
        return False
    if disruption_type == "session_cancelled":
        original = session["original"]
        return (
            slot["day"] == original["day"]
            and slot["start_minutes"] == original["start_minutes"]
            and slot["end_minutes"] == original["end_minutes"]
        )
    if day is None or slot["day"] != day:
        return False
    if whole_day or disruption_type == "day_cancelled":
        return True
    if start is None or end is None:
        return True
    return slot["start_minutes"] < end and slot["end_minutes"] > start


def _room_is_disrupted(
    report: dict[str, Any],
    slot: dict[str, Any],
    room_id: Any,
) -> bool:
    disruption_type = str(report["disruption_type"])
    if disruption_type not in {
        "room_closed",
        "room_capacity_corrected",
        "university_event",
    }:
        return False
    affected_rooms = _identity_set(report.get("affected_resource_ids") or [])
    if _normalise_identity(room_id) not in affected_rooms:
        return False
    if disruption_type == "room_capacity_corrected":
        # Until the authoritative room schedule contains the corrected value,
        # the old inventory record is not safe evidence for candidate selection.
        return True
    day, start, end, whole_day = _reported_scope(report)
    if day is None or slot["day"] != day:
        return False
    if whole_day or start is None or end is None:
        return True
    return slot["start_minutes"] < end and slot["end_minutes"] > start


def _activity_days(frozen: list[dict[str, Any]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    group_days: dict[str, set[str]] = defaultdict(set)
    staff_days: dict[str, set[str]] = defaultdict(set)
    for item in frozen:
        for group in _identity_set(item["student_groups"]):
            group_days[group].add(item["day"])
        for staff in _identity_set(item["staff_ids"]):
            staff_days[staff].add(item["day"])
    return group_days, staff_days


def _new_day_participants(
    session: dict[str, Any],
    day: str,
    group_days: dict[str, set[str]],
    staff_days: dict[str, set[str]],
) -> dict[str, list[str]]:
    if day == session["original"]["day"]:
        return {"student_groups": [], "staff": []}
    groups = [
        group
        for group in session["student_groups"]
        if day not in group_days.get(_normalise_identity(group), set())
    ]
    staff = [
        member
        for member in session["staff_ids"]
        if day not in staff_days.get(_normalise_identity(member), set())
    ]
    return {"student_groups": groups, "staff": staff}


def _availability_key(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted(_normalise_identity(value) for value in values))


def _staff_check(
    session: dict[str, Any],
    slot: dict[str, Any],
    staff_schedule_file: str,
    academic_week: int,
    cache: dict[tuple[Any, ...], dict[str, Any]],
) -> dict[str, Any]:
    key = (
        _availability_key(session["staff_ids"]),
        slot["day"],
        slot["start"],
        slot["end"],
        academic_week,
    )
    if key not in cache:
        cache[key] = _invoke(
            check_lecturer_or_ta_availability,
            {
                "uploaded_file_path": staff_schedule_file,
                "staff_ids": session["staff_ids"],
                "proposed_day": slot["day"],
                "proposed_start": slot["start"],
                "proposed_end": slot["end"],
                "academic_week": academic_week,
            },
            "check_lecturer_or_ta_availability",
        )
    return cache[key]


def _conflict_is_replaced_session(
    session: dict[str, Any],
    slot: dict[str, Any],
    conflict: dict[str, Any],
) -> bool:
    original = session["original"]
    if (
        slot["day"] != original["day"]
        or slot["start_minutes"] != original["start_minutes"]
        or slot["end_minutes"] != original["end_minutes"]
    ):
        return False
    try:
        conflict_day = _parse_day(_value(conflict, "day") or _value(conflict, "date"))
        _, conflict_start = _parse_time(_value(conflict, "start"), "conflict start")
        _, conflict_end = _parse_time(_value(conflict, "end"), "conflict end")
    except RepairInputError:
        return False
    if (
        conflict_day != original["day"]
        or conflict_start != original["start_minutes"]
        or conflict_end != original["end_minutes"]
    ):
        return False
    conflict_room = _normalise_identity(_value(conflict, "room"))
    original_room = _normalise_identity(original.get("room"))
    if not conflict_room or not original_room or conflict_room != original_room:
        return False
    conflict_course_id = _normalise_identity(_value(conflict, "course_id"))
    session_course_id = _normalise_identity(session.get("course_id"))
    conflict_course_name = _normalise_identity(_value(conflict, "course_name"))
    session_course_name = _normalise_identity(session.get("course_name"))
    return bool(
        (conflict_course_id and session_course_id and conflict_course_id == session_course_id)
        or (
            conflict_course_name
            and session_course_name
            and conflict_course_name == session_course_name
        )
    )


def _staff_effectively_available(
    payload: dict[str, Any],
    session: dict[str, Any],
    slot: dict[str, Any],
) -> tuple[bool | None, int]:
    if payload.get("status") != "success":
        return None, 0
    if payload.get("all_available") is True:
        return True, 0
    if payload.get("all_available") is None or payload.get("unknown_staff_ids"):
        return None, 0
    staff_results = payload.get("staff_results")
    if not isinstance(staff_results, list) or not staff_results:
        return False, 0

    replaced_conflicts = 0
    for staff_result in staff_results:
        if not isinstance(staff_result, dict):
            return False, 0
        if staff_result.get("available") is True:
            continue
        conflicts = staff_result.get("conflicts")
        if not isinstance(conflicts, list) or not conflicts:
            return False, 0
        if not all(
            isinstance(conflict, dict)
            and _conflict_is_replaced_session(session, slot, conflict)
            for conflict in conflicts
        ):
            return False, 0
        replaced_conflicts += len(conflicts)
    return True, replaced_conflicts


def _room_check(
    session: dict[str, Any],
    slot: dict[str, Any],
    room_schedule_file: str,
    academic_week: int,
    cache: dict[tuple[Any, ...], dict[str, Any]],
) -> dict[str, Any]:
    key = (
        slot["day"],
        slot["start"],
        slot["end"],
        academic_week,
        session["expected_students"],
        _availability_key(session["required_features"]),
        _availability_key(session["required_room_types"]),
    )
    if key not in cache:
        cache[key] = _invoke(
            check_room_availability,
            {
                "uploaded_file_path": room_schedule_file,
                "requested_day_or_date": slot["day"],
                "requested_start": slot["start"],
                "requested_end": slot["end"],
                "academic_week": academic_week,
                "minimum_capacity": session["expected_students"],
                "required_features": session["required_features"],
                "room_types": session["required_room_types"],
            },
            "check_room_availability",
        )
    return cache[key]


def _placement_cost(
    session: dict[str, Any],
    slot: dict[str, Any],
    room: dict[str, Any],
    new_day: dict[str, list[str]],
) -> tuple[float, dict[str, float]]:
    original = session["original"]
    day_distance = abs(
        DAY_INDEX[slot["day"].casefold()] - DAY_INDEX[original["day"].casefold()]
    )
    time_distance = abs(slot["start_minutes"] - original["start_minutes"]) / 30
    same_period = (
        slot.get("period") not in (None, "")
        and original.get("period") not in (None, "")
        and _normalise_identity(slot["period"]) == _normalise_identity(original["period"])
    )
    original_room = _normalise_identity(original.get("room"))
    room_changed = bool(original_room) and _normalise_identity(room.get("room")) != original_room
    capacity = room.get("capacity")
    capacity_waste = (
        max(0, int(capacity) - session["expected_students"]) / max(1, session["expected_students"])
        if capacity is not None
        else 1.0
    )
    breakdown = {
        "new_day_penalty": float(
            1000 * (len(new_day["student_groups"]) + len(new_day["staff"]))
        ),
        "day_distance": float(day_distance * 25),
        "time_distance": float(time_distance),
        "period_change": 0.0 if same_period else 15.0,
        "room_change": 20.0 if room_changed else 0.0,
        "unused_capacity": round(capacity_waste * 10, 3),
    }
    return round(sum(breakdown.values()), 3), breakdown


def _placement_conflicts(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if not _overlap(first, second):
        return False
    if _identity_set(first["student_groups"]) & _identity_set(second["student_groups"]):
        return True
    if _identity_set(first["staff_ids"]) & _identity_set(second["staff_ids"]):
        return True
    return _normalise_identity(first["room"]) == _normalise_identity(second["room"])


def _placement_id(session_key: str, slot: dict[str, Any], room: Any) -> str:
    text = f"{session_key}|{slot['day']}|{slot['start']}|{slot['end']}|{room}"
    return "PLC-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12].upper()


def _generate_session_placements(
    session: dict[str, Any],
    slots: list[dict[str, Any]],
    report: dict[str, Any],
    frozen: list[dict[str, Any]],
    group_days: dict[str, set[str]],
    staff_days: dict[str, set[str]],
    staff_schedule_file: str,
    room_schedule_file: str,
    academic_week: int,
    allow_day_off: bool,
    max_candidates: int,
    staff_cache: dict[tuple[Any, ...], dict[str, Any]],
    room_cache: dict[tuple[Any, ...], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    rejection_counts: Counter[str] = Counter()
    dependency_problems: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []

    for slot in slots:
        if slot["duration_minutes"] != session["duration_minutes"]:
            rejection_counts["duration_mismatch"] += 1
            continue
        if _slot_inside_disruption(report, session, slot):
            rejection_counts["inside_disruption_scope"] += 1
            continue
        conflict = _local_conflict(session, slot, frozen)
        if conflict:
            rejection_counts[conflict["code"]] += 1
            continue
        new_day = _new_day_participants(session, slot["day"], group_days, staff_days)
        if not allow_day_off and (new_day["student_groups"] or new_day["staff"]):
            rejection_counts["would_use_participant_day_off"] += 1
            continue

        staff_payload = _staff_check(
            session,
            slot,
            staff_schedule_file,
            academic_week,
            staff_cache,
        )
        staff_available, replaced_staff_conflicts = _staff_effectively_available(
            staff_payload, session, slot
        )
        if staff_available is not True:
            code = (
                "staff_availability_unconfirmed"
                if staff_available is None
                else "staff_unavailable"
            )
            rejection_counts[code] += 1
            if staff_payload.get("status") in {"information_required", "error"}:
                dependency_problems.append(
                    {
                        "tool": "check_lecturer_or_ta_availability",
                        "session_key": session["session_key"],
                        "slot": {key: slot[key] for key in ("day", "start", "end")},
                        "summary": staff_payload.get("summary"),
                        "required_action": staff_payload.get("required_action"),
                    }
                )
            continue

        room_payload = _room_check(
            session,
            slot,
            room_schedule_file,
            academic_week,
            room_cache,
        )
        rooms = room_payload.get("available_rooms")
        if room_payload.get("status") != "success" or not isinstance(rooms, list):
            rejection_counts["room_availability_unconfirmed"] += 1
            dependency_problems.append(
                {
                    "tool": "check_room_availability",
                    "session_key": session["session_key"],
                    "slot": {key: slot[key] for key in ("day", "start", "end")},
                    "summary": room_payload.get("summary"),
                    "required_action": room_payload.get("required_action"),
                }
            )
            continue
        if not rooms:
            rejection_counts["no_suitable_room"] += 1
            continue

        original_room = _normalise_identity(session["original"].get("room"))
        ordered_rooms = sorted(
            (
                room
                for room in rooms
                if isinstance(room, dict)
                and room.get("room")
                and not _room_is_disrupted(report, slot, room.get("room"))
            ),
            key=lambda room: (
                _normalise_identity(room.get("room")) != original_room,
                math.inf if room.get("capacity") is None else room.get("capacity"),
                _normalise_identity(room.get("room")),
            ),
        )
        disrupted_room_count = sum(
            1
            for room in rooms
            if isinstance(room, dict)
            and room.get("room")
            and _room_is_disrupted(report, slot, room.get("room"))
        )
        if disrupted_room_count:
            rejection_counts["reported_disrupted_room_excluded"] += disrupted_room_count
        if not ordered_rooms:
            rejection_counts["no_suitable_room_after_disruption_filter"] += 1
            continue
        for room in ordered_rooms[:3]:
            cost, breakdown = _placement_cost(session, slot, room, new_day)
            placements.append(
                {
                    "placement_id": _placement_id(
                        session["session_key"], slot, room.get("room")
                    ),
                    "session_key": session["session_key"],
                    "day": slot["day"],
                    "date": slot.get("date"),
                    "period": slot.get("period"),
                    "start": slot["start"],
                    "end": slot["end"],
                    "start_minutes": slot["start_minutes"],
                    "end_minutes": slot["end_minutes"],
                    "room": room.get("room"),
                    "room_type": room.get("type"),
                    "room_capacity": room.get("capacity"),
                    "room_features": room.get("features"),
                    "student_groups": session["student_groups"],
                    "staff_ids": session["staff_ids"],
                    "new_day_participants": new_day,
                    "soft_relaxations": (
                        ["participant_day_off_used"]
                        if new_day["student_groups"] or new_day["staff"]
                        else []
                    ),
                    "objective_cost": cost,
                    "objective_breakdown": breakdown,
                    "verification": {
                        "duration_preserved": True,
                        "outside_disruption_scope": True,
                        "frozen_student_conflicts": 0,
                        "frozen_staff_conflicts": 0,
                        "staff_availability_confirmed": True,
                        "original_affected_staff_booking_replaced": replaced_staff_conflicts,
                        "room_availability_confirmed": True,
                        "room_capacity_satisfied": True,
                        "room_type_and_features_satisfied": True,
                    },
                }
            )

    placements.sort(
        key=lambda item: (
            item["objective_cost"],
            DAY_INDEX[item["day"].casefold()],
            item["start_minutes"],
            _normalise_identity(item["room"]),
        )
    )
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for placement in placements:
        key = (
            placement["day"],
            placement["start"],
            placement["end"],
            _normalise_identity(placement["room"]),
        )
        unique.setdefault(key, placement)
    return list(unique.values())[:max_candidates], dict(sorted(rejection_counts.items())), dependency_problems


def _state_sort_key(state: dict[str, Any]) -> tuple[Any, ...]:
    return (
        state["unassigned_penalty"],
        len(state["unassigned"]),
        state["cost"],
        tuple(item["placement_id"] for item in state["assignments"]),
    )


def _build_options(
    sessions: list[dict[str, Any]],
    placements: dict[str, list[dict[str, Any]]],
    max_options: int,
) -> list[dict[str, Any]]:
    beam_width = max(4, max_options * 4)
    branching = min(5, max(2, max_options))
    states: list[dict[str, Any]] = [
        {
            "assignments": [],
            "unassigned": [],
            "cost": 0.0,
            "unassigned_penalty": 0,
        }
    ]
    for session in sessions:
        expanded: list[dict[str, Any]] = []
        for state in states:
            valid = [
                placement
                for placement in placements.get(session["session_key"], [])
                if not any(
                    _placement_conflicts(placement, existing)
                    for existing in state["assignments"]
                )
            ]
            if valid:
                for placement in valid[:branching]:
                    expanded.append(
                        {
                            "assignments": [*state["assignments"], placement],
                            "unassigned": list(state["unassigned"]),
                            "cost": round(state["cost"] + placement["objective_cost"], 3),
                            "unassigned_penalty": state["unassigned_penalty"],
                        }
                    )
            else:
                expanded.append(
                    {
                        "assignments": list(state["assignments"]),
                        "unassigned": [*state["unassigned"], session["session_key"]],
                        "cost": state["cost"],
                        "unassigned_penalty": (
                            state["unassigned_penalty"]
                            + 100_000
                            + session["priority_tier"] * 1_000_000
                        ),
                    }
                )
        expanded.sort(key=_state_sort_key)
        states = expanded[:beam_width]

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for state in sorted(states, key=_state_sort_key):
        signature = tuple(item["placement_id"] for item in state["assignments"])
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(state)
        if len(unique) >= max_options:
            break
    return unique


def _repair_id(disruption_id: str, assignments: list[dict[str, Any]]) -> str:
    signature = "|".join(item["placement_id"] for item in assignments)
    digest = hashlib.sha256(f"{disruption_id}|{signature}".encode("utf-8")).hexdigest()
    return "RPR-" + digest[:12].upper()


def _option_payload(
    state: dict[str, Any],
    sessions: list[dict[str, Any]],
    disruption_id: str,
    result_offset: int,
    result_limit: int,
) -> dict[str, Any]:
    assignment_map = {item["session_key"]: item for item in state["assignments"]}
    session_map = {item["session_key"]: item for item in sessions}
    repair_id = _repair_id(disruption_id, state["assignments"])
    page_sessions = sessions[result_offset : result_offset + result_limit]
    outcomes: list[dict[str, Any]] = []
    for session in page_sessions:
        placement = assignment_map.get(session["session_key"])
        if placement is None:
            outcomes.append(
                {
                    "session_key": session["session_key"],
                    "repair_order": session["repair_order"],
                    "priority_tier": session["priority_tier"],
                    "status": "unassigned",
                    "before": session["original"],
                    "after": None,
                }
            )
            continue
        outcomes.append(
            {
                "session_key": session["session_key"],
                "repair_order": session["repair_order"],
                "priority_tier": session["priority_tier"],
                "course_id": session["course_id"],
                "course_name": session["course_name"],
                "session_type": session["session_type"],
                "status": "proposed_change",
                "before": session["original"],
                "after": {
                    key: placement.get(key)
                    for key in (
                        "day",
                        "date",
                        "period",
                        "start",
                        "end",
                        "room",
                        "room_type",
                        "room_capacity",
                    )
                },
                "change_reason": "Compensation for disruption " + disruption_id,
                "objective_cost": placement["objective_cost"],
                "objective_breakdown": placement["objective_breakdown"],
                "verification": placement["verification"],
                "soft_relaxations": placement["soft_relaxations"],
                "new_day_participants": placement["new_day_participants"],
            }
        )

    changed_sessions = [session_map[key] for key in assignment_map]
    affected_staff = {
        member
        for session in changed_sessions
        for member in session["staff_ids"]
    }
    affected_groups = {
        group
        for session in changed_sessions
        for group in session["student_groups"]
    }
    rooms = {item["room"] for item in state["assignments"]}
    relaxations = Counter(
        relaxation
        for item in state["assignments"]
        for relaxation in item["soft_relaxations"]
    )
    complete = not state["unassigned"]
    return {
        "repair_id": repair_id,
        "complete": complete,
        "presentation_status": "internal_candidate_only",
        "approval_status": "blocked_pending_materialization_and_validation",
        "objective_cost": state["cost"],
        "changed_session_count": len(state["assignments"]),
        "unassigned_session_count": len(state["unassigned"]),
        "unassigned_session_keys": state["unassigned"],
        "global_outcome_order": [session["session_key"] for session in sessions],
        "session_outcomes": outcomes,
        "impact": {
            "affected_staff_reference_count": len(affected_staff),
            "affected_student_group_count": len(affected_groups),
            "replacement_room_count": len(rooms),
            "sum_expected_attendance": sum(
                session["expected_students"] for session in changed_sessions
            ),
            "attendance_note": (
                "This is a sum per changed session, not a deduplicated student count."
            ),
            "soft_constraint_relaxations": dict(sorted(relaxations.items())),
        },
        "hard_constraint_summary": {
            "unaffected_sessions_frozen": True,
            "original_duration_preserved": True,
            "student_conflicts_detected": 0,
            "staff_conflicts_detected": 0,
            "room_conflicts_detected": 0,
            "room_capacity_and_resource_requirements_checked": True,
            "complete_assignment": complete,
        },
    }


@tool
def run_schedule_repair(
    disruption_report: dict[str, Any],
    general_schedule_file: str,
    staff_schedule_file: str,
    room_schedule_file: str,
    affected_sessions: list[dict[str, Any]] | None = None,
    priority_order: list[str] | None = None,
    candidate_slots: list[dict[str, Any]] | None = None,
    sheet_name: str = "Semester Timetable",
    allow_day_off: bool = False,
    maximum_repair_options: int = 3,
    maximum_candidates_per_session: int = 20,
    result_offset: int = 0,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> str:
    """Generate conflict-screened repair options while freezing unaffected sessions.

    Pass a successful ``report_disruption`` result (or its normalized inner
    report) and the authoritative general, staff, and room schedule files. For a
    ``day_cancelled`` report, this tool retrieves every affected-session page and
    the complete global priority order itself. For other disruption types,
    provide the complete targeted ``affected_sessions`` records and an exact
    ``priority_order`` containing the same session IDs.

    Candidate slots may be supplied explicitly or derived from observed periods
    in the general timetable. A supplied period outside the observed grid must
    include ``confirmed_nonstandard=true`` after explicit user confirmation.
    Every proposed placement must preserve duration,
    avoid the disruption and all frozen student/staff conflicts, pass
    ``check_lecturer_or_ta_availability``, and use a room returned by
    ``check_room_availability`` with sufficient capacity, type, equipment, and
    accessibility. Day-off placements are rejected unless ``allow_day_off`` is
    explicitly true. The limits apply only to alternative candidates and output
    detail pages; there is no affected-session total limit.

    Results are in-memory proposals only. Materialize an exact candidate workbook
    and run ``check_validity`` before presenting it as valid or calling
    ``approve_repair``.
    """
    request = {
        "general_schedule_file": general_schedule_file,
        "staff_schedule_file": staff_schedule_file,
        "room_schedule_file": room_schedule_file,
        "sheet_name": sheet_name,
        "allow_day_off": allow_day_off,
        "maximum_repair_options": maximum_repair_options,
        "maximum_candidates_per_session": maximum_candidates_per_session,
        "result_offset": result_offset,
        "result_limit": result_limit,
    }
    try:
        report = _unwrap_report(disruption_report)
        for field, value in (
            ("general_schedule_file", general_schedule_file),
            ("staff_schedule_file", staff_schedule_file),
            ("room_schedule_file", room_schedule_file),
            ("sheet_name", sheet_name),
        ):
            if not isinstance(value, str) or not value.strip():
                raise RepairInputError(f"{field} is required and must be nonempty.")
        if not isinstance(allow_day_off, bool):
            raise RepairInputError("allow_day_off must be true or false.")
        if (
            isinstance(maximum_repair_options, bool)
            or not isinstance(maximum_repair_options, int)
            or not 1 <= maximum_repair_options <= 5
        ):
            raise RepairInputError("maximum_repair_options must be between 1 and 5.")
        if (
            isinstance(maximum_candidates_per_session, bool)
            or not isinstance(maximum_candidates_per_session, int)
            or not 1 <= maximum_candidates_per_session <= 50
        ):
            raise RepairInputError(
                "maximum_candidates_per_session must be between 1 and 50."
            )
        if isinstance(result_offset, bool) or not isinstance(result_offset, int) or result_offset < 0:
            raise RepairInputError("result_offset must be a non-negative integer.")
        if (
            isinstance(result_limit, bool)
            or not isinstance(result_limit, int)
            or not 1 <= result_limit <= MAX_RESULT_LIMIT
        ):
            raise RepairInputError(
                f"result_limit must be between 1 and {MAX_RESULT_LIMIT}."
            )
        scope = report["scope"]
        academic_week = scope.get("academic_week")
        if (
            isinstance(academic_week, bool)
            or not isinstance(academic_week, int)
            or not 1 <= academic_week <= 53
        ):
            raise RepairInputError(
                "The normalized disruption report must contain academic_week from 1 to 53."
            )
    except RepairInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "repair_complete": False,
                "summary": str(exc),
                "request": request,
                "repair_options": [],
            }
        )

    try:
        schedule_rows, schedule_retrieval = _retrieve_complete_schedule(
            general_schedule_file.strip(), sheet_name.strip()
        )
        disruption_type = str(report["disruption_type"])
        if disruption_type == "day_cancelled":
            affected_day = report["scope"].get("affected_date") or report["scope"].get(
                "affected_day"
            )
            if not affected_day:
                raise RepairInputError(
                    "A day_cancelled report must contain affected_day or affected_date."
                )
            raw_sessions, affected_retrieval = _collect_cancelled_day_sessions(
                general_schedule_file.strip(),
                sheet_name.strip(),
                str(affected_day),
                academic_week,
            )
            affected_keys = {
                _normalise_identity(_value(session, "session_key"))
                for session in raw_sessions
            }
            ordered_keys, priority_retrieval = _collect_priority_order(
                general_schedule_file.strip(),
                sheet_name.strip(),
                str(affected_day),
                academic_week,
                affected_keys,
            )
        else:
            if not isinstance(affected_sessions, list) or not affected_sessions:
                raise RepairInputError(
                    "Complete affected_sessions are required for non-day disruptions."
                )
            if any(not isinstance(item, dict) for item in affected_sessions):
                raise RepairInputError("Every affected_sessions item must be an object.")
            raw_sessions = list(affected_sessions)
            ordered_keys = _clean_priority_order(priority_order)
            keys = [_normalise_identity(_value(item, "session_key")) for item in raw_sessions]
            if any(not key for key in keys) or len(keys) != len(set(keys)):
                raise RepairInputError(
                    "affected_sessions require unique nonempty stable identifiers."
                )
            if {_normalise_identity(key) for key in ordered_keys} != set(keys):
                raise RepairInputError(
                    "priority_order must contain every affected session exactly once."
                )
            affected_retrieval = {
                "source": "provided_targeted_scope",
                "affected_session_count": len(raw_sessions),
                "complete": True,
            }
            priority_retrieval = {
                "source": "provided_priority_order",
                "ranked_session_count": len(ordered_keys),
                "ranking_complete": True,
            }
    except RepairInputError as exc:
        return _json(
            {
                "status": "information_required",
                "repair_complete": False,
                "summary": str(exc),
                "request": request,
                "repair_options": [],
                "required_action": "Correct the repair scope before candidate generation.",
            }
        )
    except RepairDependencyError as exc:
        return _json(
            {
                "status": "information_required",
                "repair_complete": False,
                "summary": str(exc),
                "request": request,
                "dependency_details": exc.details,
                "repair_options": [],
                "required_action": (
                    "Resolve the incomplete schedule, affected-session, or priority retrieval before repair."
                ),
            }
        )

    raw_by_key = {
        _normalise_identity(_value(item, "session_key")): item for item in raw_sessions
    }
    normalized_sessions: list[dict[str, Any]] = []
    missing_session_data: list[dict[str, Any]] = []
    for position, key in enumerate(ordered_keys, start=1):
        raw = raw_by_key.get(_normalise_identity(key))
        if raw is None:
            missing_session_data.append(
                {"session_key": key, "missing_fields": ["affected session record"]}
            )
            continue
        normalized, missing = _normalize_session(raw, position)
        if normalized is None:
            missing_session_data.append(
                {"session_key": key, "missing_fields": missing}
            )
        else:
            normalized_sessions.append(normalized)
    if missing_session_data:
        return _json(
            {
                "status": "information_required",
                "repair_complete": False,
                "summary": (
                    f"{len(missing_session_data)} affected session(s) lack mandatory repair data."
                ),
                "disruption_id": report["disruption_id"],
                "missing_session_data": missing_session_data,
                "repair_options": [],
                "required_action": (
                    "Correct or confirm every listed session field before generating placements."
                ),
            }
        )

    if result_offset > len(normalized_sessions):
        return _json(
            {
                "status": "invalid_request",
                "repair_complete": False,
                "summary": "result_offset is beyond the affected-session result set.",
                "affected_session_count": len(normalized_sessions),
                "repair_options": [],
            }
        )
    if not normalized_sessions:
        return _json(
            {
                "status": "success",
                "repair_complete": True,
                "summary": "No active sessions require compensation for this disruption.",
                "disruption_id": report["disruption_id"],
                "affected_session_count": 0,
                "repair_options": [],
                "approval_status": "not_applicable",
            }
        )

    affected_key_set = {
        _normalise_identity(session["session_key"]) for session in normalized_sessions
    }
    frozen: list[dict[str, Any]] = []
    frozen_uncertainties: list[dict[str, Any]] = []
    for row in schedule_rows:
        if _normalise_identity(_value(row, "session_key")) in affected_key_set:
            continue
        item, issue = _normalize_frozen_row(row, academic_week)
        if issue:
            frozen_uncertainties.append(
                {
                    "source": {
                        "sheet": row.get("_source_sheet"),
                        "table": row.get("_source_table"),
                        "row": row.get("_source_row"),
                    },
                    "issue": issue,
                }
            )
        elif item:
            frozen.append(item)
    if frozen_uncertainties:
        return _json(
            {
                "status": "information_required",
                "repair_complete": False,
                "summary": (
                    "The unaffected timetable contains unreadable scope or time data, so conflicts cannot be excluded."
                ),
                "disruption_id": report["disruption_id"],
                "unreadable_frozen_rows": frozen_uncertainties[:100],
                "unreadable_frozen_row_count": len(frozen_uncertainties),
                "repair_options": [],
                "required_action": (
                    "Correct the reported source rows before schedule repair."
                ),
            }
        )

    try:
        slots, slot_source = _normalize_candidate_slots(candidate_slots, schedule_rows)
    except RepairInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "repair_complete": False,
                "summary": str(exc),
                "disruption_id": report["disruption_id"],
                "repair_options": [],
            }
        )

    group_days, staff_days = _activity_days(frozen)
    staff_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    room_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    placement_map: dict[str, list[dict[str, Any]]] = {}
    diagnostics: list[dict[str, Any]] = []
    dependency_problems: list[dict[str, Any]] = []

    try:
        for session in normalized_sessions:
            placements, rejected, problems = _generate_session_placements(
                session=session,
                slots=slots,
                report=report,
                frozen=frozen,
                group_days=group_days,
                staff_days=staff_days,
                staff_schedule_file=staff_schedule_file.strip(),
                room_schedule_file=room_schedule_file.strip(),
                academic_week=academic_week,
                allow_day_off=allow_day_off,
                max_candidates=maximum_candidates_per_session,
                staff_cache=staff_cache,
                room_cache=room_cache,
            )
            placement_map[session["session_key"]] = placements
            diagnostics.append(
                {
                    "session_key": session["session_key"],
                    "repair_order": session["repair_order"],
                    "eligible_candidate_count": len(placements),
                    "rejected_candidate_counts": rejected,
                }
            )
            dependency_problems.extend(problems)
    except RepairDependencyError as exc:
        return _json(
            {
                "status": "information_required",
                "repair_complete": False,
                "summary": str(exc),
                "disruption_id": report["disruption_id"],
                "dependency_details": exc.details,
                "repair_options": [],
                "required_action": "Resolve the availability-tool failure before repair.",
            }
        )

    states = _build_options(
        normalized_sessions,
        placement_map,
        maximum_repair_options,
    )
    options = [
        _option_payload(
            state,
            normalized_sessions,
            str(report["disruption_id"]),
            result_offset,
            result_limit,
        )
        for state in states
    ]
    complete_options = sum(1 for option in options if option["complete"])
    all_session_candidates_exist = all(
        placement_map.get(session["session_key"]) for session in normalized_sessions
    )
    repair_complete = complete_options > 0
    status = "success" if repair_complete else "information_required"
    page_end = min(result_offset + result_limit, len(normalized_sessions))
    has_more = page_end < len(normalized_sessions)

    response: dict[str, Any] = {
        "status": status,
        "repair_complete": repair_complete,
        "summary": (
            f"{complete_options} complete internal repair option(s) were generated for "
            f"{len(normalized_sessions)} affected session(s)."
            if repair_complete
            else (
                "No complete conflict-screened repair option could be generated under the confirmed constraints."
            )
        ),
        "disruption_id": report["disruption_id"],
        "affected_session_count": len(normalized_sessions),
        "unaffected_session_count_frozen": len(frozen),
        "analysis_complete_for_all_affected_sessions": True,
        "all_sessions_have_independent_candidates": all_session_candidates_exist,
        "complete_repair_option_count": complete_options,
        "repair_options": options,
        "candidate_generation": {
            "candidate_slot_source": slot_source,
            "candidate_slot_count": len(slots),
            "maximum_candidates_per_session": maximum_candidates_per_session,
            "staff_availability_checks_cached": len(staff_cache),
            "room_availability_checks_cached": len(room_cache),
            "session_diagnostics": diagnostics[
                result_offset : result_offset + result_limit
            ],
            "dependency_problem_count": len(dependency_problems),
            "dependency_problems": dependency_problems[:100],
            "search_policy": {
                "method": "priority-ordered bounded beam search",
                "global_optimality_claimed": False,
                "evaluated_candidate_limit_per_session": maximum_candidates_per_session,
                "interpretation": (
                    "Options are the lowest-cost conflict-screened combinations among the retained candidates; "
                    "they are not proof that no other feasible timetable exists."
                ),
            },
        },
        "retrieval": {
            "general_schedule": schedule_retrieval,
            "affected_sessions": affected_retrieval,
            "priority_order": priority_retrieval,
        },
        "constraint_policy": {
            "priority_order": "Exam/Quiz > Lecture > Laboratory > Tutorial",
            "unaffected_sessions_frozen": True,
            "hard_conflicts_allowed": False,
            "day_off_placement_allowed": allow_day_off,
            "day_off_rule": (
                "Explicitly allowed; every use is reported as a soft relaxation."
                if allow_day_off
                else "Rejected unless a later user-authorized run sets allow_day_off=true."
            ),
            "reported_hard_constraints": report.get("hard_constraints") or [],
            "reported_hard_constraints_relaxed": [],
        },
        "result_pagination": {
            "result_offset": result_offset,
            "result_limit": result_limit,
            "returned_range": [result_offset + 1, page_end] if normalized_sessions else [],
            "has_more": has_more,
            "next_result_offset": page_end if has_more else None,
            "page_is_final": not has_more,
            "instruction": (
                "Call run_schedule_repair again with result_offset set to next_result_offset for the next detailed outcome page."
                if has_more
                else "This is the final detailed outcome page."
            ),
        },
        "validation_handoff": {
            "status": "not_run",
            "reason": (
                "check_validity requires an exact materialized repaired workbook; these are in-memory proposals."
            ),
            "next_tool": "check_validity",
            "required_action": (
                "Materialize one exact repair option in a new candidate workbook, compare it with the original, then run check_validity on all authoritative schedule files."
            ),
            "ready_for_approval": False,
            "approve_repair_blocked": True,
        },
    }
    if not repair_complete:
        response["required_action"] = (
            "Review the per-session rejection diagnostics, provide missing schedule data, add confirmed candidate periods, or explicitly authorize a reported soft-constraint relaxation."
        )
    return _json(response)
