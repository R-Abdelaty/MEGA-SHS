"""Find every scheduled session affected by cancelling one university day."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from langchain.tools import tool

from .get_schedule import get_schedule


MAX_ROWS_PER_CALL = 500
MAX_CHARS_PER_CALL = 120_000
MAX_PAGINATION_CALLS = 100
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 100

WEEKDAYS = {
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
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

FIELD_ALIASES: dict[str, set[str]] = {
    "session_id": {"sessionid", "eventid", "bookingid", "activityid"},
    "course_id": {"courseid", "coursecode", "moduleid", "modulecode"},
    "course_name": {"coursename", "modulename", "subject", "subjectname"},
    "session_type": {"sessiontype", "activitytype", "classtype", "type"},
    "day": {"day", "weekday", "dayofweek", "sessionday"},
    "date": {"date", "sessiondate", "scheduleddate", "scheduledate"},
    "period": {"period", "periodid", "slot", "timeslot"},
    "start": {"start", "starttime", "sessionstart", "scheduledstart", "from"},
    "end": {"end", "endtime", "sessionend", "scheduledend", "to"},
    "instructor": {
        "instructor",
        "instructorname",
        "lecturer",
        "lecturername",
        "doctor",
        "doctorname",
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
    "room": {"room", "roomid", "venue", "location"},
    "room_type": {"roomtype", "venuetype", "requiredroomtype"},
    "equipment": {
        "equipment",
        "requiredequipment",
        "equipmentrequirements",
        "features",
        "facilities",
    },
    "accessibility_requirements": {
        "accessibility",
        "accessibilityrequirements",
        "accessrequirements",
        "specialrequirements",
    },
    "faculty": {"faculty", "facultyname", "school"},
    "major": {"major", "majors", "majorcode", "majorcodes", "program"},
    "year": {"year", "academicyear", "studyyear"},
    "section": {"section", "sectionid"},
    "student_groups": {
        "cohortgroup",
        "cohortgroups",
        "studentgroup",
        "studentgroups",
        "tutorialgroup",
        "tutorialgroups",
        "groupid",
    },
    "expected_students": {
        "expectedstudents",
        "studentcount",
        "enrollment",
        "enrolment",
    },
    "week": {
        "week",
        "weeks",
        "weeknumber",
        "academicweek",
        "academicweeks",
        "semesterweek",
        "teachingweek",
        "teachingweeks",
    },
    "start_week": {"startweek", "firstweek", "weekfrom"},
    "end_week": {"endweek", "lastweek", "weekto"},
    "status": {"status", "sessionstatus", "bookingstatus", "activitystatus"},
}

INACTIVE_STATUSES = {
    "cancelled",
    "canceled",
    "deleted",
    "inactive",
    "postponed",
    "removed",
}


class AffectedSessionsInputError(ValueError):
    """Raised when the cancellation scope is invalid or ambiguous."""


class ScheduleRetrievalError(RuntimeError):
    """Raised when get_schedule cannot return a complete affected-day scope."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalise_text(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _parse_day(value: str) -> tuple[str, date | None]:
    text = str(value).strip()
    if not text:
        raise AffectedSessionsInputError("affected_day_or_date is required.")

    key = text.casefold().rstrip(".")
    if key in WEEKDAYS:
        return WEEKDAY_LABELS[WEEKDAYS[key]], None

    try:
        exact_date = date.fromisoformat(text)
    except ValueError as exc:
        raise AffectedSessionsInputError(
            "affected_day_or_date must be a weekday or an ISO date in YYYY-MM-DD format."
        ) from exc
    return exact_date.strftime("%A"), exact_date


def _decode_schedule_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "content"):
        raw_result = raw_result.content
    if isinstance(raw_result, dict):
        payload = raw_result
    elif isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise ScheduleRetrievalError(
                "get_schedule returned invalid JSON.",
                {"reason": str(exc)},
            ) from exc
    else:
        raise ScheduleRetrievalError(
            "get_schedule returned an unsupported result type.",
            {"result_type": type(raw_result).__name__},
        )

    if not isinstance(payload, dict):
        raise ScheduleRetrievalError("get_schedule did not return a JSON object.")
    if str(payload.get("status", "")).casefold() == "error":
        error = payload.get("error")
        details = error if isinstance(error, dict) else {"error": error}
        raise ScheduleRetrievalError(
            str(details.get("message") or "get_schedule could not retrieve the timetable."),
            details,
        )
    return payload


def _invoke_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_result = get_schedule.invoke(arguments)
    except Exception as exc:
        raise ScheduleRetrievalError(
            "get_schedule could not be invoked.",
            {"exception_type": type(exc).__name__, "reason": str(exc)},
        ) from exc
    return _decode_schedule_result(raw_result)


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    extraction = payload.get("extraction")
    if not isinstance(extraction, dict):
        return rows

    sheets = extraction.get("sheets")
    if not isinstance(sheets, list):
        return rows
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        sheet_name = str(sheet.get("name") or "")
        tables = sheet.get("tables")
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            table_name = str(table.get("name") or "")
            for row in table.get("rows", []):
                if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                    continue
                rows.append(
                    {
                        **row["values"],
                        "_source_sheet": sheet_name,
                        "_source_table": table_name,
                        "_source_row": row.get("excel_row"),
                    }
                )
    return rows


def _row_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("_source_sheet") or ""),
        str(row.get("_source_table") or ""),
        str(row.get("_source_row") or ""),
    )


def _retrieve_complete_day(
    uploaded_file_path: str,
    sheet_name: str,
    weekday: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    calls = 0
    rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str, str]] = set()
    matching_rows_found: int | None = None

    while True:
        calls += 1
        if calls > MAX_PAGINATION_CALLS:
            raise ScheduleRetrievalError(
                "The affected-day schedule exceeded the safe pagination limit.",
                {"pagination_calls_completed": calls - 1},
            )

        payload = _invoke_schedule(
            {
                "uploaded_file_path": uploaded_file_path,
                "sheet_name": sheet_name,
                "filters": {"day": weekday},
                "row_offset": offset,
                "max_rows": MAX_ROWS_PER_CALL,
                "max_chars": MAX_CHARS_PER_CALL,
            }
        )
        extraction = payload.get("extraction")
        limits = payload.get("limits")
        if not isinstance(extraction, dict) or not isinstance(limits, dict):
            raise ScheduleRetrievalError(
                "get_schedule omitted completeness metadata.",
                {"required_fields": ["extraction", "limits"]},
            )

        found = extraction.get("matching_rows_found")
        if isinstance(found, int):
            if matching_rows_found is not None and found != matching_rows_found:
                raise ScheduleRetrievalError(
                    "The schedule changed while affected sessions were being paginated.",
                    {
                        "initial_matching_rows": matching_rows_found,
                        "current_matching_rows": found,
                    },
                )
            matching_rows_found = found

        for row in _extract_rows(payload):
            identity = _row_identity(row)
            if identity in seen_rows:
                continue
            seen_rows.add(identity)
            rows.append(row)

        has_more = extraction.get("has_more")
        if has_more is False:
            if limits.get("truncated") is True or limits.get("completeness") != "complete":
                raise ScheduleRetrievalError(
                    "The final schedule page is incomplete and cannot be used for cancellation.",
                    {"limits": limits},
                )
            break
        if has_more is not True:
            raise ScheduleRetrievalError(
                "get_schedule did not state whether more matching sessions exist.",
                {"has_more": has_more},
            )

        next_offset = extraction.get("next_row_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise ScheduleRetrievalError(
                "get_schedule returned an invalid pagination offset.",
                {"current_offset": offset, "next_row_offset": next_offset},
            )
        offset = next_offset

    if matching_rows_found is not None and len(rows) != matching_rows_found:
        raise ScheduleRetrievalError(
            "The complete affected-day row count could not be verified.",
            {
                "matching_rows_found": matching_rows_found,
                "unique_rows_retrieved": len(rows),
            },
        )

    return rows, {
        "pagination_calls": calls,
        "matching_rows_found": matching_rows_found,
        "unique_rows_retrieved": len(rows),
        "complete": True,
    }


def _record_map(row: dict[str, Any]) -> dict[str, Any]:
    return {
        _normalise_key(key): value
        for key, value in row.items()
        if not str(key).startswith("_")
    }


def _value(row: dict[str, Any], canonical_field: str) -> Any:
    record = _record_map(row)
    for alias in FIELD_ALIASES[canonical_field]:
        value = record.get(alias)
        if value not in (None, ""):
            return value
    return None


def _parse_weeks(value: Any) -> set[int]:
    if value in (None, ""):
        return set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}

    text = str(value).strip().casefold()
    if text in {"all", "all weeks", "every week", "weekly", "recurring"}:
        return set()
    text = text.replace("–", "-").replace("—", "-")
    weeks: set[int] = set()
    for start, end in re.findall(r"(\d+)\s*-\s*(\d+)", text):
        low, high = sorted((int(start), int(end)))
        weeks.update(range(low, high + 1))
    without_ranges = re.sub(r"\d+\s*-\s*\d+", " ", text)
    weeks.update(int(item) for item in re.findall(r"\d+", without_ranges))
    return weeks


def _row_applies_to_week(row: dict[str, Any], academic_week: int) -> tuple[bool, bool]:
    start_week = _value(row, "start_week")
    end_week = _value(row, "end_week")
    if start_week not in (None, "") or end_week not in (None, ""):
        try:
            low = int(start_week if start_week not in (None, "") else end_week)
            high = int(end_week if end_week not in (None, "") else start_week)
        except (TypeError, ValueError):
            return False, True
        low, high = sorted((low, high))
        return low <= academic_week <= high, False

    raw_week = _value(row, "week")
    if raw_week in (None, ""):
        return True, False
    parsed_weeks = _parse_weeks(raw_week)
    if parsed_weeks:
        return academic_week in parsed_weeks, False
    if _normalise_text(raw_week) in {
        "all",
        "all weeks",
        "every week",
        "weekly",
        "recurring",
    }:
        return True, False
    return False, True


def _parse_record_date(value: Any) -> date | None:
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


def _duration_minutes(start_value: Any, end_value: Any) -> int | None:
    def parse(value: Any) -> int | None:
        text = str(value or "").strip().upper().replace(".", "")
        for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
            try:
                parsed = datetime.strptime(text, pattern)
                return parsed.hour * 60 + parsed.minute
            except ValueError:
                continue
        return None

    start = parse(start_value)
    end = parse(end_value)
    if start is None or end is None or end <= start:
        return None
    return end - start


def _additional_source_fields(row: dict[str, Any]) -> dict[str, Any]:
    consumed_aliases = {
        alias
        for aliases in FIELD_ALIASES.values()
        for alias in aliases
    }
    return {
        key: value
        for key, value in row.items()
        if not str(key).startswith("_")
        and _normalise_key(key) not in consumed_aliases
        and value not in (None, "")
    }


def _compact_session(row: dict[str, Any]) -> dict[str, Any]:
    session: dict[str, Any] = {}
    for field in (
        "session_id",
        "course_id",
        "course_name",
        "session_type",
        "day",
        "date",
        "period",
        "start",
        "end",
        "instructor",
        "instructor_id",
        "teaching_assistant",
        "teaching_assistant_id",
        "room",
        "room_type",
        "equipment",
        "accessibility_requirements",
        "faculty",
        "major",
        "year",
        "section",
        "student_groups",
        "expected_students",
        "week",
        "status",
    ):
        value = _value(row, field)
        if value not in (None, ""):
            session[field] = value

    session["source"] = {
        "sheet": row.get("_source_sheet"),
        "table": row.get("_source_table"),
        "excel_row": row.get("_source_row"),
    }
    session["affected_session_key"] = str(
        session.get("session_id")
        or (
            f"{row.get('_source_sheet')}:{row.get('_source_table')}:"
            f"{row.get('_source_row')}"
        )
    )

    additional_fields = _additional_source_fields(row)
    if additional_fields:
        session["additional_source_fields"] = additional_fields

    duration = _duration_minutes(session.get("start"), session.get("end"))
    required_staff = {
        key: session[key]
        for key in (
            "instructor",
            "instructor_id",
            "teaching_assistant",
            "teaching_assistant_id",
        )
        if key in session
    }
    requirements: dict[str, Any] = {
        "original_day": session.get("day"),
        "original_date": session.get("date"),
        "original_period": session.get("period"),
        "original_start": session.get("start"),
        "original_end": session.get("end"),
        "duration_minutes": duration,
        "session_type": session.get("session_type"),
        "required_staff": required_staff,
        "student_groups": session.get("student_groups"),
        "minimum_room_capacity": session.get("expected_students"),
        "original_room": session.get("room"),
        "required_room_type": session.get("room_type"),
        "required_equipment": session.get("equipment"),
        "accessibility_requirements": session.get("accessibility_requirements"),
        "academic_week_rule": session.get("week"),
    }
    session["compensation_requirements"] = {
        key: value
        for key, value in requirements.items()
        if value not in (None, "", {})
    }

    required_fields = {
        "session_id": session.get("session_id"),
        "course_id": session.get("course_id"),
        "session_type": session.get("session_type"),
        "start": session.get("start"),
        "end": session.get("end"),
        "staff": required_staff,
        "student_groups": session.get("student_groups"),
        "expected_students": session.get("expected_students"),
        "room": session.get("room"),
        "duration_minutes": duration,
    }
    session["missing_compensation_fields"] = [
        field
        for field, value in required_fields.items()
        if value in (None, "", {})
    ]
    session["compensation_status"] = "pending"
    return session


def _impact_summary(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(item.get("session_type") or "Unspecified") for item in sessions)
    course_ids = {str(item["course_id"]) for item in sessions if item.get("course_id")}
    rooms = {str(item["room"]) for item in sessions if item.get("room")}
    staff = {
        str(value)
        for item in sessions
        for value in (
            item.get("instructor"),
            item.get("instructor_id"),
            item.get("teaching_assistant"),
            item.get("teaching_assistant_id"),
        )
        if value not in (None, "")
    }
    groups = {
        group.strip()
        for item in sessions
        for group in re.split(r"[;,|\n]+", str(item.get("student_groups") or ""))
        if group.strip()
    }
    expected_attendance_total = sum(
        int(float(item["expected_students"]))
        for item in sessions
        if item.get("expected_students") not in (None, "")
        and str(item["expected_students"]).replace(".", "", 1).isdigit()
    )
    incomplete = [
        item["affected_session_key"]
        for item in sessions
        if item.get("missing_compensation_fields")
    ]
    return {
        "sessions_by_type": dict(sorted(by_type.items())),
        "unique_course_count": len(course_ids),
        "unique_staff_reference_count": len(staff),
        "unique_room_count": len(rooms),
        "unique_student_group_count": len(groups),
        "sum_expected_attendance": expected_attendance_total,
        "attendance_note": (
            "This is the sum of expected attendance per session, not a deduplicated student count."
        ),
        "sessions_missing_compensation_fields": len(incomplete),
        "session_keys_requiring_clarification": incomplete,
    }


def _filter_scope(
    rows: list[dict[str, Any]],
    academic_week: int,
    exact_date: date | None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    included: list[dict[str, Any]] = []
    counts = {
        "already_inactive": 0,
        "outside_academic_week": 0,
        "outside_exact_date": 0,
        "unreadable_week": 0,
        "unreadable_date": 0,
    }
    warnings: list[str] = []

    for row in rows:
        status = _normalise_text(_value(row, "status"))
        if status in INACTIVE_STATUSES:
            counts["already_inactive"] += 1
            continue

        applies, ambiguous_week = _row_applies_to_week(row, academic_week)
        if ambiguous_week:
            counts["unreadable_week"] += 1
            continue
        if not applies:
            counts["outside_academic_week"] += 1
            continue

        if exact_date is not None:
            raw_date = _value(row, "date")
            if raw_date not in (None, ""):
                record_date = _parse_record_date(raw_date)
                if record_date is None:
                    counts["unreadable_date"] += 1
                    continue
                if record_date != exact_date:
                    counts["outside_exact_date"] += 1
                    continue

        included.append(_compact_session(row))

    if counts["unreadable_week"]:
        warnings.append(
            f"{counts['unreadable_week']} session(s) had unreadable week data and were not included."
        )
    if counts["unreadable_date"]:
        warnings.append(
            f"{counts['unreadable_date']} session(s) had unreadable date data and were not included."
        )
    return included, counts, warnings


@tool
def find_affected_sessions(
    uploaded_file_path: str,
    affected_day_or_date: str,
    academic_week: int,
    sheet_name: str = "Semester Timetable",
    result_offset: int = 0,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> str:
    """Return every active session affected by cancelling one day in one week.

    Retrieve the requested weekday through ``get_schedule`` using structured
    filtering and continue through every result page. Then apply the requested
    academic week, exclude already inactive sessions, and return detailed
    compensation handoff records with source sheet/table/row evidence. Every
    otherwise-unmapped source field is preserved. Detailed records are returned
    in deterministic pages; while ``has_more`` is true, call the tool again with
    ``result_offset`` set to the returned ``next_result_offset``. This tool is
    read-only: it identifies the cancellation scope but does not cancel or
    reschedule it.

    ``affected_day_or_date`` accepts a weekday such as ``Sunday`` or an ISO date
    such as ``2026-09-06``. ``academic_week`` is mandatory because the same
    weekday recurs throughout the semester.
    """
    request = {
        "uploaded_file_path": uploaded_file_path,
        "sheet_name": sheet_name,
        "affected_day_or_date": affected_day_or_date,
        "academic_week": academic_week,
        "result_offset": result_offset,
        "result_limit": result_limit,
    }
    try:
        if not str(uploaded_file_path).strip():
            raise AffectedSessionsInputError("uploaded_file_path is required.")
        if not str(sheet_name).strip():
            raise AffectedSessionsInputError("sheet_name is required.")
        if not isinstance(academic_week, int) or isinstance(academic_week, bool) or academic_week < 1:
            raise AffectedSessionsInputError("academic_week must be a positive integer.")
        if not isinstance(result_offset, int) or isinstance(result_offset, bool) or result_offset < 0:
            raise AffectedSessionsInputError("result_offset must be a non-negative integer.")
        if (
            not isinstance(result_limit, int)
            or isinstance(result_limit, bool)
            or not 1 <= result_limit <= MAX_RESULT_LIMIT
        ):
            raise AffectedSessionsInputError(
                f"result_limit must be between 1 and {MAX_RESULT_LIMIT}."
            )
        weekday, exact_date = _parse_day(affected_day_or_date)
    except AffectedSessionsInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "summary": str(exc),
                "request": request,
                "complete": False,
                "affected_session_count": 0,
                "affected_sessions": [],
            }
        )

    try:
        day_rows, retrieval = _retrieve_complete_day(
            uploaded_file_path=str(uploaded_file_path).strip(),
            sheet_name=str(sheet_name).strip(),
            weekday=weekday,
        )
    except ScheduleRetrievalError as exc:
        return _json(
            {
                "status": "information_required",
                "summary": "The complete affected-day scope could not be verified.",
                "request": request,
                "complete": False,
                "affected_session_count": 0,
                "affected_sessions": [],
                "reason": str(exc),
                "details": exc.details,
                "required_action": (
                    "Correct the schedule source or retrieval issue before cancelling the day."
                ),
            }
        )

    affected, excluded_counts, warnings = _filter_scope(
        rows=day_rows,
        academic_week=academic_week,
        exact_date=exact_date,
    )
    if excluded_counts["unreadable_week"] or excluded_counts["unreadable_date"]:
        return _json(
            {
                "status": "information_required",
                "summary": (
                    "Some sessions could not be classified safely; the cancellation scope is incomplete."
                ),
                "request": {
                    **request,
                    "resolved_weekday": weekday,
                    "resolved_date": exact_date.isoformat() if exact_date else None,
                },
                "complete": False,
                "affected_session_count": 0,
                "affected_sessions": [],
                "retrieval": retrieval,
                "excluded_counts": excluded_counts,
                "warnings": warnings,
                "required_action": (
                    "Correct or confirm the unreadable week/date fields before cancelling the day."
                ),
            }
        )

    impact_summary = _impact_summary(affected)
    ready_for_compensation = impact_summary["sessions_missing_compensation_fields"] == 0
    if result_offset > len(affected):
        return _json(
            {
                "status": "invalid_request",
                "summary": "result_offset is beyond the affected-session result set.",
                "request": {
                    **request,
                    "resolved_weekday": weekday,
                    "resolved_date": exact_date.isoformat() if exact_date else None,
                },
                "complete": True,
                "affected_session_count": len(affected),
                "affected_sessions": [],
                "required_action": (
                    "Use result_offset 0 or a previously returned next_result_offset."
                ),
            }
        )

    page_end = min(result_offset + result_limit, len(affected))
    affected_page = affected[result_offset:page_end]
    has_more_results = page_end < len(affected)

    return _json(
        {
            "status": "success",
            "summary": (
                f"{len(affected)} active session(s) are affected by cancelling "
                f"{weekday}, academic week {academic_week}. Detailed records "
                f"{result_offset + 1 if affected_page else 0}-{page_end} are returned."
            ),
            "request": {
                **request,
                "resolved_weekday": weekday,
                "resolved_date": exact_date.isoformat() if exact_date else None,
            },
            "complete": True,
            "affected_session_count": len(affected),
            "returned_session_count": len(affected_page),
            "affected_sessions": affected_page,
            "result_pagination": {
                "result_offset": result_offset,
                "result_limit": result_limit,
                "returned_range": (
                    [result_offset + 1, page_end]
                    if affected_page
                    else []
                ),
                "has_more": has_more_results,
                "next_result_offset": page_end if has_more_results else None,
                "page_is_final": not has_more_results,
                "all_details_returned_in_this_response": (
                    not has_more_results and result_offset == 0
                ),
                "instruction": (
                    "Call find_affected_sessions again with result_offset set to "
                    "next_result_offset until has_more is false. Process compensation "
                    "in the same bounded batches."
                    if has_more_results
                    else "This is the final detailed result page."
                ),
            },
            "impact_summary": impact_summary,
            "compensation_handoff": {
                "ready_for_compensation": ready_for_compensation,
                "total_record_count": len(affected),
                "records_in_this_page": len(affected_page),
                "record_key": "affected_session_key",
                "status": (
                    "ready"
                    if ready_for_compensation
                    else "clarification_required_for_some_sessions"
                ),
                "required_next_checks": [
                    "check_priority",
                    "check_lecturer_or_ta_availability",
                    "check_room_availability",
                    "run_schedule_repair",
                    "check_validity",
                ],
                "instruction": (
                    "Preserve each session's duration, staff, student groups, capacity, "
                    "academic requirements, and resource requirements during compensation."
                ),
            },
            "retrieval": retrieval,
            "excluded_counts": excluded_counts,
            "warnings": warnings,
        }
    )
