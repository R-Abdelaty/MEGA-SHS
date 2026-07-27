"""Detailed, UI-ready comparison of original and repaired schedule versions."""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain.tools import tool

from .get_schedule import get_schedule


MAX_ROWS_PER_CALL = 500
MAX_CHARS_PER_CALL = 120_000
MAX_PAGINATION_CALLS = 100
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 100

COLOR_TOKENS: dict[str, dict[str, str]] = {
    "added": {
        "label": "Added",
        "color_name": "green",
        "foreground": "#166534",
        "background": "#DCFCE7",
        "border": "#22C55E",
        "symbol": "+",
    },
    "removed": {
        "label": "Removed",
        "color_name": "red",
        "foreground": "#991B1B",
        "background": "#FEE2E2",
        "border": "#EF4444",
        "symbol": "-",
    },
    "modified": {
        "label": "Modified",
        "color_name": "amber",
        "foreground": "#92400E",
        "background": "#FEF3C7",
        "border": "#F59E0B",
        "symbol": "~",
    },
    "unchanged": {
        "label": "Unchanged",
        "color_name": "gray",
        "foreground": "#475569",
        "background": "#F1F5F9",
        "border": "#94A3B8",
        "symbol": "=",
    },
    "before": {
        "label": "Before",
        "color_name": "red",
        "foreground": "#991B1B",
        "background": "#FEF2F2",
        "border": "#FCA5A5",
        "symbol": "<",
    },
    "after": {
        "label": "After",
        "color_name": "green",
        "foreground": "#166534",
        "background": "#F0FDF4",
        "border": "#86EFAC",
        "symbol": ">",
    },
    "information": {
        "label": "Information",
        "color_name": "blue",
        "foreground": "#1E40AF",
        "background": "#DBEAFE",
        "border": "#60A5FA",
        "symbol": "i",
    },
}

SESSION_KEY_ALIASES = {
    "sessionid",
    "eventid",
    "bookingid",
    "activityid",
    "examid",
    "assessmentid",
}

CANONICAL_ALIASES: dict[str, set[str]] = {
    "session_id": SESSION_KEY_ALIASES,
    "course_id": {"courseid", "coursecode", "moduleid", "modulecode"},
    "course_name": {"coursename", "modulename", "subjectname", "subject"},
    "session_type": {
        "sessiontype",
        "activitytype",
        "assessmenttype",
        "examtype",
        "classtype",
        "type",
    },
    "day": {"day", "weekday", "dayofweek"},
    "date": {"date", "sessiondate", "examdate", "assessmentdate", "scheduleddate"},
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
    "room": {"room", "roomid", "venue", "location", "hall"},
    "room_assignments": {
        "roomassignments",
        "roomassignmentsstudents",
        "assignedrooms",
        "roomallocation",
        "roomallocations",
    },
    "room_type": {"roomtype", "venuetype", "requiredroomtype"},
    "room_capacity": {"roomcapacity", "capacity", "seatcapacity"},
    "room_count": {"roomcount", "numberofrooms"},
    "instructor": {
        "instructor",
        "instructorname",
        "lecturer",
        "lecturername",
        "doctor",
        "doctorname",
        "staff",
    },
    "instructor_id": {"instructorid", "lecturerid", "doctorid", "staffid"},
    "teaching_assistant": {
        "ta",
        "taname",
        "teachingassistant",
        "teachingassistantname",
    },
    "teaching_assistant_id": {"taid", "teachingassistantid"},
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
        "students",
        "studentcount",
        "enrollment",
        "enrolment",
        "candidates",
    },
    "faculty": {"faculty", "facultyname", "school"},
    "major": {"major", "majors", "majorcode", "majorcodes", "program"},
    "year": {"year", "academicyear", "studyyear"},
    "section": {"section", "sectionid"},
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
    "status": {"status", "sessionstatus", "bookingstatus", "examstatus"},
    "priority": {"priority", "prioritylevel", "academicpriority"},
    "mode": {"mode", "deliverymode", "assessmentmode"},
    "assessment_number": {"number", "assessmentnumber", "examnumber"},
    "duration_periods": {"durationperiods", "numberofperiods"},
    "change_reason": {"changereason", "reasonforchange", "repairreason", "reason"},
}

DISPLAY_LABELS = {
    "session_id": "Session ID",
    "course_id": "Course ID",
    "course_name": "Course Name",
    "session_type": "Session Type",
    "day": "Day",
    "date": "Date",
    "period": "Period",
    "start": "Start Time",
    "end": "End Time",
    "week": "Academic Week",
    "room": "Room",
    "room_assignments": "Room Assignments",
    "room_type": "Room Type",
    "room_capacity": "Room Capacity",
    "room_count": "Room Count",
    "instructor": "Instructor",
    "instructor_id": "Instructor ID",
    "teaching_assistant": "Teaching Assistant",
    "teaching_assistant_id": "Teaching Assistant ID",
    "student_groups": "Student Groups",
    "expected_students": "Expected Students",
    "faculty": "Faculty",
    "major": "Major",
    "year": "Year",
    "section": "Section",
    "equipment": "Equipment",
    "accessibility": "Accessibility Requirements",
    "status": "Status",
    "priority": "Priority",
    "mode": "Mode",
    "assessment_number": "Assessment Number",
    "duration_periods": "Duration (Periods)",
    "change_reason": "Change Reason",
    "source_sheet": "Schedule Section",
}

TIMING_FIELDS = {"day", "date", "period", "start", "end", "week", "duration_periods"}
ROOM_FIELDS = {
    "room",
    "room_assignments",
    "room_type",
    "room_capacity",
    "room_count",
    "equipment",
    "accessibility",
}
STAFF_FIELDS = {
    "instructor",
    "instructor_id",
    "teaching_assistant",
    "teaching_assistant_id",
}
STUDENT_FIELDS = {"student_groups", "expected_students", "section"}
ACADEMIC_FIELDS = {
    "course_id",
    "course_name",
    "session_type",
    "faculty",
    "major",
    "year",
    "priority",
    "mode",
    "assessment_number",
}


class ComparisonInputError(ValueError):
    """Raised when a comparison request is invalid."""


class VersionRetrievalError(RuntimeError):
    """Raised when a complete schedule version cannot be retrieved."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _canonical_key(header: Any) -> str:
    normalised = _normalise_key(header)
    for canonical, aliases in CANONICAL_ALIASES.items():
        if normalised in aliases:
            return canonical
    return f"source_{normalised}"


def _decode_tool_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "content"):
        raw_result = raw_result.content
    if isinstance(raw_result, dict):
        payload = raw_result
    elif isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise VersionRetrievalError(
                "get_schedule returned invalid JSON.",
                {"reason": str(exc)},
            ) from exc
    else:
        raise VersionRetrievalError(
            "get_schedule returned an unsupported result type.",
            {"result_type": type(raw_result).__name__},
        )
    if not isinstance(payload, dict):
        raise VersionRetrievalError("get_schedule did not return a JSON object.")
    if str(payload.get("status", "")).casefold() == "error":
        error = payload.get("error")
        details = error if isinstance(error, dict) else {"error": error}
        raise VersionRetrievalError(
            str(details.get("message") or "get_schedule could not retrieve a schedule version."),
            details,
        )
    return payload


def _invoke_get_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = get_schedule.invoke(arguments)
    except Exception as exc:
        raise VersionRetrievalError(
            "get_schedule could not be invoked.",
            {"exception_type": type(exc).__name__, "reason": str(exc)},
        ) from exc
    return _decode_tool_result(result)


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
        for table in sheet.get("tables", []):
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


def _retrieve_sheet(file_path: str, sheet_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    offset = 0
    calls = 0
    expected_count: int | None = None
    rows: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str, str]] = set()

    while True:
        calls += 1
        if calls > MAX_PAGINATION_CALLS:
            raise VersionRetrievalError(
                "Schedule retrieval exceeded the safe pagination limit.",
                {"file": file_path, "sheet": sheet_name},
            )
        payload = _invoke_get_schedule(
            {
                "uploaded_file_path": file_path,
                "sheet_name": sheet_name,
                "row_offset": offset,
                "max_rows": MAX_ROWS_PER_CALL,
                "max_chars": MAX_CHARS_PER_CALL,
            }
        )
        extraction = payload.get("extraction")
        limits = payload.get("limits")
        if not isinstance(extraction, dict) or not isinstance(limits, dict):
            raise VersionRetrievalError(
                "get_schedule omitted completeness metadata.",
                {"file": file_path, "sheet": sheet_name},
            )

        found = extraction.get("matching_rows_found")
        if isinstance(found, int):
            if expected_count is not None and found != expected_count:
                raise VersionRetrievalError(
                    "The schedule changed while it was being retrieved.",
                    {
                        "file": file_path,
                        "sheet": sheet_name,
                        "initial_count": expected_count,
                        "current_count": found,
                    },
                )
            expected_count = found

        for row in _extract_rows(payload):
            identity = (
                str(row.get("_source_sheet") or ""),
                str(row.get("_source_table") or ""),
                str(row.get("_source_row") or ""),
            )
            if identity not in seen_sources:
                seen_sources.add(identity)
                rows.append(row)

        has_more = extraction.get("has_more")
        if has_more is False:
            if limits.get("truncated") is True or limits.get("completeness") != "complete":
                raise VersionRetrievalError(
                    "The final schedule page is incomplete.",
                    {"file": file_path, "sheet": sheet_name, "limits": limits},
                )
            break
        if has_more is not True:
            raise VersionRetrievalError(
                "get_schedule did not state whether more rows exist.",
                {"file": file_path, "sheet": sheet_name, "has_more": has_more},
            )
        next_offset = extraction.get("next_row_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise VersionRetrievalError(
                "get_schedule returned an invalid pagination offset.",
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "current_offset": offset,
                    "next_offset": next_offset,
                },
            )
        offset = next_offset

    if expected_count is not None and len(rows) != expected_count:
        raise VersionRetrievalError(
            "The retrieved row count does not match the complete schedule count.",
            {
                "file": file_path,
                "sheet": sheet_name,
                "expected": expected_count,
                "retrieved": len(rows),
            },
        )
    return rows, {
        "sheet": sheet_name,
        "row_count": len(rows),
        "pagination_calls": calls,
        "complete": True,
    }


def _retrieve_version(file_path: str, sheet_names: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    for sheet_name in sheet_names:
        sheet_rows, sheet_metadata = _retrieve_sheet(file_path, sheet_name)
        rows.extend(sheet_rows)
        metadata.append(sheet_metadata)
    return rows, metadata


def _field_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for header, value in row.items():
        if str(header).startswith("_"):
            continue
        canonical = _canonical_key(header)
        if canonical in fields and fields[canonical]["value"] not in (None, ""):
            continue
        fields[canonical] = {
            "label": DISPLAY_LABELS.get(canonical, str(header)),
            "source_header": str(header),
            "value": value,
        }
    fields["source_sheet"] = {
        "label": DISPLAY_LABELS["source_sheet"],
        "source_header": "_source_sheet",
        "value": row.get("_source_sheet"),
    }
    return fields


def _session_key(
    row: dict[str, Any],
    session_id_field: str | None,
) -> str | None:
    requested_key = _normalise_key(session_id_field) if session_id_field else None
    for header, value in row.items():
        if str(header).startswith("_") or value in (None, ""):
            continue
        normalised = _normalise_key(header)
        if requested_key and normalised == requested_key:
            return str(value).strip()
        if not requested_key and normalised in SESSION_KEY_ALIASES:
            return str(value).strip()
    return None


def _index_version(
    rows: list[dict[str, Any]],
    version_label: str,
    session_id_field: str | None,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    duplicates: list[str] = []
    for row in rows:
        key = _session_key(row, session_id_field)
        if not key:
            missing.append(
                {
                    "sheet": row.get("_source_sheet"),
                    "table": row.get("_source_table"),
                    "excel_row": row.get("_source_row"),
                }
            )
            continue
        if key in indexed:
            duplicates.append(key)
        else:
            indexed[key] = row

    if missing or duplicates:
        raise VersionRetrievalError(
            f"The {version_label} schedule lacks unique stable session identifiers.",
            {
                "version": version_label,
                "session_id_field": session_id_field or "automatic aliases",
                "missing_identifier_count": len(missing),
                "missing_identifier_examples": missing[:20],
                "duplicate_identifiers": sorted(set(duplicates))[:100],
            },
        )
    return indexed


def _normalised_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return str(value)
    return " ".join(str(value).strip().split()).casefold()


def _values_equal(before: Any, after: Any) -> bool:
    return _normalised_value(before) == _normalised_value(after)


def _field_category(field: str) -> str:
    if field in TIMING_FIELDS:
        return "schedule_time"
    if field in ROOM_FIELDS:
        return "room_or_resource"
    if field in STAFF_FIELDS:
        return "staff"
    if field in STUDENT_FIELDS:
        return "students"
    if field in ACADEMIC_FIELDS:
        return "academic"
    if field == "status":
        return "status"
    if field == "source_sheet":
        return "schedule_section"
    if field == "change_reason":
        return "reason"
    return "other"


def _snapshot(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        entry["label"]: entry["value"]
        for entry in fields.values()
        if entry["value"] not in (None, "")
    }


def _summary(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "course_id",
        "course_name",
        "session_type",
        "day",
        "date",
        "period",
        "start",
        "end",
        "week",
        "room",
        "room_assignments",
        "room_count",
        "instructor",
        "teaching_assistant",
        "student_groups",
        "expected_students",
        "faculty",
        "major",
        "status",
        "source_sheet",
    ):
        if key in fields and fields[key]["value"] not in (None, ""):
            summary[key] = fields[key]["value"]
    return summary


def _time_phrase(fields: dict[str, dict[str, Any]]) -> str:
    day = fields.get("date", fields.get("day", {})).get("value")
    start = fields.get("start", {}).get("value")
    end = fields.get("end", {}).get("value")
    period = fields.get("period", {}).get("value")
    pieces = [str(value) for value in (day, period) if value not in (None, "")]
    if start not in (None, "") or end not in (None, ""):
        pieces.append(f"{start or '?'}-{end or '?'}")
    return ", ".join(pieces) or "an unspecified time"


def _describe_change(
    status: str,
    session_key: str,
    before: dict[str, dict[str, Any]] | None,
    after: dict[str, dict[str, Any]] | None,
    categories: set[str],
) -> str:
    active = after or before or {}
    course = active.get("course_name", active.get("course_id", {})).get("value")
    subject = f"{session_key} ({course})" if course else session_key
    if status == "added":
        return f"Added {subject} at {_time_phrase(active)}."
    if status == "removed":
        return f"Removed {subject}, previously scheduled at {_time_phrase(active)}."

    statements: list[str] = []
    if "schedule_time" in categories and before and after:
        statements.append(
            f"rescheduled from {_time_phrase(before)} to {_time_phrase(after)}"
        )
    if "room_or_resource" in categories:
        statements.append("changed room or resource requirements")
    if "staff" in categories:
        statements.append("changed assigned staff")
    if "students" in categories:
        statements.append("changed student-group or attendance information")
    if "academic" in categories:
        statements.append("changed academic session information")
    if "status" in categories:
        statements.append("changed session status")
    if "schedule_section" in categories:
        statements.append("moved the session to a different schedule section")
    if not statements:
        statements.append("changed session details")
    return f"Modified {subject}: " + "; ".join(statements) + "."


def _compare_versions(
    original: dict[str, dict[str, Any]],
    repaired: dict[str, dict[str, Any]],
    include_unchanged: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    changes: list[dict[str, Any]] = []
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}

    all_keys = sorted(set(original) | set(repaired), key=str.casefold)
    for session_key in all_keys:
        before_row = original.get(session_key)
        after_row = repaired.get(session_key)
        before_fields = _field_map(before_row) if before_row else None
        after_fields = _field_map(after_row) if after_row else None

        if before_fields is None:
            counts["added"] += 1
            changes.append(
                {
                    "session_key": session_key,
                    "change_status": "added",
                    "description": _describe_change(
                        "added", session_key, None, after_fields, {"added"}
                    ),
                    "reason": (
                        after_fields.get("change_reason", {}).get("value")
                        or "No change reason is recorded in the repaired schedule."
                    ),
                    "after": _snapshot(after_fields),
                    "after_source": {
                        "sheet": after_row.get("_source_sheet"),
                        "table": after_row.get("_source_table"),
                        "excel_row": after_row.get("_source_row"),
                    },
                    "display": COLOR_TOKENS["added"],
                }
            )
            continue
        if after_fields is None:
            counts["removed"] += 1
            changes.append(
                {
                    "session_key": session_key,
                    "change_status": "removed",
                    "description": _describe_change(
                        "removed", session_key, before_fields, None, {"removed"}
                    ),
                    "reason": "No removal reason is recorded in the repaired schedule.",
                    "before": _snapshot(before_fields),
                    "before_source": {
                        "sheet": before_row.get("_source_sheet"),
                        "table": before_row.get("_source_table"),
                        "excel_row": before_row.get("_source_row"),
                    },
                    "display": COLOR_TOKENS["removed"],
                }
            )
            continue

        field_changes: list[dict[str, Any]] = []
        categories: set[str] = set()
        for field in sorted(set(before_fields) | set(after_fields)):
            before_entry = before_fields.get(field, {})
            after_entry = after_fields.get(field, {})
            before_value = before_entry.get("value")
            after_value = after_entry.get("value")
            if _values_equal(before_value, after_value):
                continue
            category = _field_category(field)
            categories.add(category)
            field_changes.append(
                {
                    "field": field,
                    "label": (
                        after_entry.get("label")
                        or before_entry.get("label")
                        or DISPLAY_LABELS.get(field, field)
                    ),
                    "category": category,
                    "before": before_value,
                    "after": after_value,
                    "before_display": COLOR_TOKENS["before"],
                    "after_display": COLOR_TOKENS["after"],
                }
            )

        if field_changes:
            counts["modified"] += 1
            reason = after_fields.get("change_reason", {}).get("value")
            changes.append(
                {
                    "session_key": session_key,
                    "change_status": "modified",
                    "description": _describe_change(
                        "modified", session_key, before_fields, after_fields, categories
                    ),
                    "reason": reason or "No change reason is recorded in the repaired schedule.",
                    "change_categories": sorted(categories),
                    "changed_field_count": len(field_changes),
                    "field_changes": field_changes,
                    "before_summary": _summary(before_fields),
                    "after_summary": _summary(after_fields),
                    "before_source": {
                        "sheet": before_row.get("_source_sheet"),
                        "table": before_row.get("_source_table"),
                        "excel_row": before_row.get("_source_row"),
                    },
                    "after_source": {
                        "sheet": after_row.get("_source_sheet"),
                        "table": after_row.get("_source_table"),
                        "excel_row": after_row.get("_source_row"),
                    },
                    "display": COLOR_TOKENS["modified"],
                }
            )
        else:
            counts["unchanged"] += 1
            if include_unchanged:
                changes.append(
                    {
                        "session_key": session_key,
                        "change_status": "unchanged",
                        "description": f"No schedule fields changed for {session_key}.",
                        "summary": _summary(after_fields),
                        "display": COLOR_TOKENS["unchanged"],
                    }
                )

    order = {"removed": 0, "modified": 1, "added": 2, "unchanged": 3}
    changes.sort(key=lambda item: (order[item["change_status"]], item["session_key"].casefold()))
    return changes, counts


def _number(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _split_references(value: Any) -> set[str]:
    return {
        item.strip()
        for item in re.split(r"[;,|\n]+", str(value or ""))
        if item.strip()
    }


def _split_room_assignments(value: Any) -> set[str]:
    rooms: set[str] = set()
    for assignment in re.split(r"[;,|\n]+", str(value or "")):
        room = re.sub(r"\s*\([^)]*\)\s*$", "", assignment).strip()
        if room:
            rooms.add(room)
    return rooms


def _impact_report(changes: list[dict[str, Any]]) -> dict[str, Any]:
    active_changes = [item for item in changes if item["change_status"] != "unchanged"]
    categories: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    staff: set[str] = set()
    rooms: set[str] = set()
    groups: set[str] = set()
    faculties: set[str] = set()
    majors: set[str] = set()
    attendance_instances = 0

    for change in active_changes:
        for category in change.get("change_categories", []):
            categories[category] += 1
        for field_change in change.get("field_changes", []):
            fields[field_change["label"]] += 1

        snapshot = (
            change.get("after")
            or change.get("after_summary")
            or change.get("before")
            or change.get("before_summary")
            or {}
        )
        canonical_snapshot = {
            _canonical_key(key): value for key, value in snapshot.items()
        }
        attendance_instances += _number(canonical_snapshot.get("expected_students"))
        for field in STAFF_FIELDS:
            staff.update(_split_references(canonical_snapshot.get(field)))
        rooms.update(_split_references(canonical_snapshot.get("room")))
        rooms.update(_split_room_assignments(canonical_snapshot.get("room_assignments")))
        groups.update(_split_references(canonical_snapshot.get("student_groups")))
        faculties.update(_split_references(canonical_snapshot.get("faculty")))
        majors.update(_split_references(canonical_snapshot.get("major")))

    status_counts = Counter(item["change_status"] for item in active_changes)
    return {
        "affected_session_count": len(active_changes),
        "changes_by_status": dict(sorted(status_counts.items())),
        "changes_by_category": dict(sorted(categories.items())),
        "changed_fields": dict(sorted(fields.items())),
        "affected_staff_reference_count": len(staff),
        "affected_room_count": len(rooms),
        "affected_student_group_count": len(groups),
        "affected_faculty_count": len(faculties),
        "affected_major_count": len(majors),
        "sum_expected_attendance_for_changed_sessions": attendance_instances,
        "attendance_note": (
            "This is a sum of expected attendance per changed session, not a "
            "deduplicated count of individual students."
        ),
    }


@tool
def compare_schedule_versions(
    original_file_path: str,
    repaired_file_path: str,
    sheet_names: list[str],
    session_id_field: str | None = None,
    include_unchanged: bool = False,
    result_offset: int = 0,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> str:
    """Compare complete original and repaired schedules with UI-ready colors.

    Retrieve every row from each named worksheet through ``get_schedule`` and
    match sessions by a stable identifier. Return counts for the complete
    comparison plus paginated, field-level before/after details. Added sessions
    are green, removed sessions red, modified sessions amber, and unchanged
    sessions gray. Color names, accessible labels, and hex tokens are supplied
    for the website UI; color is never the only indicator.

    ``sheet_names`` is mandatory so the tool never guesses which workbook tabs
    represent schedules. If automatic ID aliases do not match the uploaded
    headers, provide the exact ``session_id_field``. This tool compares versions
    but does not validate, approve, or apply the repaired schedule.
    """
    request = {
        "original_file_path": original_file_path,
        "repaired_file_path": repaired_file_path,
        "sheet_names": sheet_names,
        "session_id_field": session_id_field,
        "include_unchanged": include_unchanged,
        "result_offset": result_offset,
        "result_limit": result_limit,
    }
    try:
        if not str(original_file_path).strip() or not str(repaired_file_path).strip():
            raise ComparisonInputError("Both original_file_path and repaired_file_path are required.")
        if not isinstance(sheet_names, list):
            raise ComparisonInputError("sheet_names must be a list of worksheet names.")
        cleaned_sheets: list[str] = []
        seen_sheets: set[str] = set()
        for value in sheet_names:
            name = str(value).strip()
            key = name.casefold()
            if name and key not in seen_sheets:
                cleaned_sheets.append(name)
                seen_sheets.add(key)
        if not cleaned_sheets:
            raise ComparisonInputError("At least one schedule worksheet name is required.")
        if not isinstance(include_unchanged, bool):
            raise ComparisonInputError("include_unchanged must be true or false.")
        if not isinstance(result_offset, int) or isinstance(result_offset, bool) or result_offset < 0:
            raise ComparisonInputError("result_offset must be a non-negative integer.")
        if (
            not isinstance(result_limit, int)
            or isinstance(result_limit, bool)
            or not 1 <= result_limit <= MAX_RESULT_LIMIT
        ):
            raise ComparisonInputError(
                f"result_limit must be between 1 and {MAX_RESULT_LIMIT}."
            )
    except ComparisonInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "summary": str(exc),
                "request": request,
                "complete": False,
                "changes": [],
            }
        )

    try:
        original_rows, original_retrieval = _retrieve_version(
            str(original_file_path).strip(), cleaned_sheets
        )
        repaired_rows, repaired_retrieval = _retrieve_version(
            str(repaired_file_path).strip(), cleaned_sheets
        )
        original_index = _index_version(original_rows, "original", session_id_field)
        repaired_index = _index_version(repaired_rows, "repaired", session_id_field)
    except VersionRetrievalError as exc:
        return _json(
            {
                "status": "information_required",
                "summary": "The complete schedule comparison could not be verified.",
                "request": {**request, "sheet_names": cleaned_sheets},
                "complete": False,
                "changes": [],
                "reason": str(exc),
                "details": exc.details,
                "required_action": (
                    "Correct the file, worksheet, or stable session-ID mapping before comparison."
                ),
            }
        )

    changes, counts = _compare_versions(
        original=original_index,
        repaired=repaired_index,
        include_unchanged=include_unchanged,
    )
    if result_offset > len(changes):
        return _json(
            {
                "status": "invalid_request",
                "summary": "result_offset is beyond the comparison result set.",
                "request": {**request, "sheet_names": cleaned_sheets},
                "complete": True,
                "total_change_records": len(changes),
                "changes": [],
            }
        )

    page_end = min(result_offset + result_limit, len(changes))
    change_page = changes[result_offset:page_end]
    has_more = page_end < len(changes)
    changed_count = counts["added"] + counts["removed"] + counts["modified"]
    impact = _impact_report(changes)

    if changed_count:
        concise_summary = (
            f"{changed_count} session(s) changed: {counts['added']} added, "
            f"{counts['removed']} removed, and {counts['modified']} modified. "
            f"{counts['unchanged']} session(s) were preserved unchanged."
        )
    else:
        concise_summary = (
            f"No schedule changes were detected; all {counts['unchanged']} sessions are unchanged."
        )

    return _json(
        {
            "status": "success",
            "summary": concise_summary,
            "complete": True,
            "comparison_scope": {
                "original_file": str(original_file_path).strip(),
                "repaired_file": str(repaired_file_path).strip(),
                "sheet_names": cleaned_sheets,
                "session_id_field": session_id_field or "automatic stable-ID aliases",
                "original_session_count": len(original_index),
                "repaired_session_count": len(repaired_index),
            },
            "change_totals": {
                **counts,
                "changed": changed_count,
                "detail_records_available": len(changes),
            },
            "impact_report": impact,
            "ui_presentation": {
                "title": "Schedule Repair Impact Report",
                "concise_summary": concise_summary,
                "color_legend": [
                    COLOR_TOKENS[key]
                    for key in ("added", "removed", "modified", "unchanged")
                ],
                "accessibility_note": (
                    "Every color is paired with a text label and symbol; do not use color alone."
                ),
            },
            "returned_change_count": len(change_page),
            "changes": change_page,
            "result_pagination": {
                "result_offset": result_offset,
                "result_limit": result_limit,
                "returned_range": [result_offset + 1, page_end] if change_page else [],
                "has_more": has_more,
                "next_result_offset": page_end if has_more else None,
                "page_is_final": not has_more,
                "instruction": (
                    "Call compare_schedule_versions again with result_offset set to "
                    "next_result_offset until has_more is false."
                    if has_more
                    else "This is the final comparison detail page."
                ),
            },
            "retrieval": {
                "original": original_retrieval,
                "repaired": repaired_retrieval,
                "complete": True,
            },
            "validation": {
                "repair_validated": False,
                "approval_granted": False,
                "required_next_action": (
                    "Run check_validity on the repaired schedule before recommending or approving it."
                ),
            },
        }
    )
