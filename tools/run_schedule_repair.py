"""Apply one exact schedule repair in memory.

This tool is deliberately a small component in the scheduling workflow.  It
does not discover sessions, search for alternatives, call sibling tools, read
or write workbooks, validate availability, approve a repair, or make policy
decisions.  The parent scheduler must supply one complete, preselected set of
assignments after the other tools have done that work.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from typing import Any

from langchain.tools import tool


SESSION_ID_ALIASES = {
    "affectedsessionkey",
    "sessionkey",
    "sessionid",
    "eventid",
    "bookingid",
    "activityid",
    "examid",
    "assessmentid",
}

FIELD_ALIASES: dict[str, set[str]] = {
    "day": {"day", "weekday", "dayofweek", "sessionday"},
    "date": {"date", "sessiondate", "scheduleddate", "examdate"},
    "period": {"period", "slot", "timeslot"},
    "period_id": {"periodid", "periodcode"},
    "start": {"start", "starttime", "from", "sessionstart", "examstart"},
    "end": {"end", "endtime", "to", "sessionend", "examend"},
    "room": {"room", "roomid", "venue", "location", "hall"},
    "week": {
        "week",
        "weeks",
        "weeknumber",
        "academicweek",
        "semesterweek",
        "teachingweek",
    },
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


class RepairInputError(ValueError):
    """Raised when the exact repair cannot be applied safely."""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalise_identity(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _field_name(record: dict[str, Any], aliases: set[str]) -> str | None:
    matches = [key for key in record if _normalise_key(key) in aliases]
    if len(matches) > 1:
        values = {_normalise_identity(record[key]) for key in matches}
        if len(values) > 1:
            raise RepairInputError(
                "A schedule row contains conflicting duplicate fields: "
                + ", ".join(matches)
            )
    return matches[0] if matches else None


def _value(record: dict[str, Any], aliases: set[str]) -> Any:
    key = _field_name(record, aliases)
    return record.get(key) if key else None


def _session_key(record: dict[str, Any], session_id_field: str | None) -> str:
    if session_id_field:
        if session_id_field not in record:
            raise RepairInputError(
                f"The session ID field {session_id_field!r} is missing from a schedule row."
            )
        value = record.get(session_id_field)
    else:
        value = _value(record, SESSION_ID_ALIASES)
    return str(value or "").strip()


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


def _parse_day(value: Any, field: str) -> str:
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


def _unwrap_report(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepairInputError("disruption_report must be an object.")
    if "disruption_report" in value:
        if value.get("status") != "success" or value.get("report_complete") is not True:
            raise RepairInputError(
                "disruption_report must be a complete successful report_disruption result."
            )
        report = value.get("disruption_report")
    else:
        report = value
    if not isinstance(report, dict):
        raise RepairInputError("disruption_report does not contain a report object.")
    for field in ("disruption_id", "disruption_type", "scope"):
        if report.get(field) in (None, ""):
            raise RepairInputError(f"disruption_report is missing {field}.")
    if not isinstance(report["scope"], dict):
        raise RepairInputError("disruption_report.scope must be an object.")
    return report


def _clean_affected_keys(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise RepairInputError("affected_session_keys must be a nonempty list.")
    cleaned = [str(value).strip() for value in values]
    if any(not value for value in cleaned):
        raise RepairInputError("affected_session_keys cannot contain blank values.")
    normalised = [_normalise_identity(value) for value in cleaned]
    if len(normalised) != len(set(normalised)):
        raise RepairInputError("affected_session_keys must be unique.")
    return cleaned


def _normalise_assignment(record: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RepairInputError(f"repair_assignments[{index}] must be an object.")
    key = _session_key(record, None)
    if not key:
        raise RepairInputError(
            f"repair_assignments[{index}] requires a stable session ID."
        )
    raw_date = _value(record, FIELD_ALIASES["date"])
    raw_day = _value(record, FIELD_ALIASES["day"])
    if raw_date not in (None, ""):
        date_text = str(raw_date).strip()[:10]
        try:
            derived_day = date.fromisoformat(date_text).strftime("%A")
        except ValueError as exc:
            raise RepairInputError(
                f"repair_assignments[{index}].date must use YYYY-MM-DD format."
            ) from exc
        day = _parse_day(raw_day, f"repair_assignments[{index}].day") if raw_day else derived_day
        if day != derived_day:
            raise RepairInputError(
                f"repair_assignments[{index}] has a day that does not match its date."
            )
    else:
        date_text = None
        day = _parse_day(raw_day, f"repair_assignments[{index}].day")

    start, start_minutes = _parse_time(
        _value(record, FIELD_ALIASES["start"]),
        f"repair_assignments[{index}].start",
    )
    end, end_minutes = _parse_time(
        _value(record, FIELD_ALIASES["end"]),
        f"repair_assignments[{index}].end",
    )
    if end_minutes <= start_minutes:
        raise RepairInputError(
            f"repair_assignments[{index}].end must be later than start."
        )
    room = str(_value(record, FIELD_ALIASES["room"]) or "").strip()
    if not room:
        raise RepairInputError(f"repair_assignments[{index}].room is required.")
    period = _value(record, FIELD_ALIASES["period"])
    period_id = _value(record, FIELD_ALIASES["period_id"])
    raw_week = _value(record, FIELD_ALIASES["week"])
    if raw_week in (None, ""):
        academic_week = None
    else:
        match = re.search(r"\d+", str(raw_week))
        if not match or not 1 <= int(match.group()) <= 53:
            raise RepairInputError(
                f"repair_assignments[{index}].week must identify academic week 1-53."
            )
        academic_week = int(match.group())
    return {
        "session_key": key,
        "day": day,
        "date": date_text,
        "period": period,
        "period_id": period_id,
        "start": start,
        "end": end,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "room": room,
        "week": academic_week,
    }


def _same_scope_day(scope: dict[str, Any], assignment: dict[str, Any]) -> bool:
    disrupted_week = scope.get("academic_week")
    assignment_week = assignment.get("week")
    if assignment_week is not None and disrupted_week is not None:
        if assignment_week != disrupted_week:
            return False
    affected_date = scope.get("affected_date")
    if affected_date not in (None, ""):
        return assignment.get("date") == str(affected_date).strip()[:10]
    affected_day = scope.get("affected_day")
    if affected_day in (None, ""):
        return False
    return assignment["day"] == _parse_day(affected_day, "disruption_report.scope.affected_day")


def _overlaps_disruption(report: dict[str, Any], assignment: dict[str, Any]) -> bool:
    scope = report["scope"]
    if not _same_scope_day(scope, assignment):
        return False
    if scope.get("whole_day") is True or report["disruption_type"] == "day_cancelled":
        return True
    if scope.get("start_time") in (None, "") or scope.get("end_time") in (None, ""):
        return False
    _, disruption_start = _parse_time(
        scope["start_time"], "disruption_report.scope.start_time"
    )
    _, disruption_end = _parse_time(
        scope["end_time"], "disruption_report.scope.end_time"
    )
    return assignment["start_minutes"] < disruption_end and disruption_start < assignment["end_minutes"]


def _validate_assignment_against_report(
    report: dict[str, Any], assignment: dict[str, Any]
) -> None:
    disruption_type = str(report["disruption_type"]).strip().casefold()
    overlaps = _overlaps_disruption(report, assignment)
    if disruption_type in {
        "day_cancelled",
        "partial_day_cancelled",
        "lecturer_or_ta_unavailable",
    } and overlaps:
        raise RepairInputError(
            f"Assignment for {assignment['session_key']} remains inside the disruption scope."
        )
    if disruption_type == "room_closed" and overlaps:
        affected_rooms = {
            _normalise_identity(value)
            for value in report.get("affected_resource_ids") or []
        }
        if _normalise_identity(assignment["room"]) in affected_rooms:
            raise RepairInputError(
                f"Assignment for {assignment['session_key']} still uses the closed room."
            )


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        field: _value(row, aliases)
        for field, aliases in FIELD_ALIASES.items()
        if _value(row, aliases) not in (None, "")
    }


def _replace_field(
    row: dict[str, Any], canonical: str, value: Any, session_key: str
) -> None:
    field = _field_name(row, FIELD_ALIASES[canonical])
    if field is None:
        raise RepairInputError(
            f"Schedule row {session_key!r} has no field for {canonical!r}; "
            "provide a schedule with explicit repairable columns."
        )
    row[field] = value


def _apply_assignment(
    row: dict[str, Any], assignment: dict[str, Any], session_key: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = _snapshot(row)
    original_start, original_start_minutes = _parse_time(
        before.get("start"), f"schedule row {session_key}.start"
    )
    original_end, original_end_minutes = _parse_time(
        before.get("end"), f"schedule row {session_key}.end"
    )
    original_duration = original_end_minutes - original_start_minutes
    replacement_duration = assignment["end_minutes"] - assignment["start_minutes"]
    if original_duration <= 0:
        raise RepairInputError(f"Schedule row {session_key!r} has an invalid time range.")
    if replacement_duration != original_duration:
        raise RepairInputError(
            f"Assignment for {session_key!r} changes the session duration from "
            f"{original_duration} to {replacement_duration} minutes."
        )

    _replace_field(row, "day", assignment["day"], session_key)
    _replace_field(row, "start", assignment["start"], session_key)
    _replace_field(row, "end", assignment["end"], session_key)
    _replace_field(row, "room", assignment["room"], session_key)
    if assignment["date"] is not None:
        _replace_field(row, "date", assignment["date"], session_key)
    if assignment["period"] not in (None, ""):
        _replace_field(row, "period", assignment["period"], session_key)
    if assignment["period_id"] not in (None, ""):
        period_id_field = _field_name(row, FIELD_ALIASES["period_id"])
        if period_id_field is not None:
            row[period_id_field] = assignment["period_id"]
    if assignment["week"] is not None:
        _replace_field(row, "week", f"Week {assignment['week']}", session_key)

    return before, _snapshot(row)


@tool
def run_schedule_repair(
    disruption_report: dict[str, Any],
    schedule_rows: list[dict[str, Any]],
    affected_session_keys: list[str],
    repair_assignments: list[dict[str, Any]],
    session_id_field: str | None = None,
) -> str:
    """Return one in-memory schedule with exact supplied assignments applied.

    This is a subordinate transformation tool, not a scheduling agent. The
    parent workflow must first retrieve the complete schedule, identify every
    affected session, establish priority, check staff and rooms, and select one
    exact assignment for each affected session. Pass those results here.

    ``schedule_rows`` must contain the complete original schedule.
    ``affected_session_keys`` defines the exact repair scope.
    ``repair_assignments`` must contain exactly one preselected assignment for
    every affected key, including session ID, day (or ISO date), start, end, and
    room; period and academic week are optional. ``session_id_field`` may name the exact ID column
    when automatic aliases are unsuitable.

    The tool deep-copies the rows and changes only the supplied sessions. It
    performs structural safety checks but does not independently search,
    validate availability, write a workbook, modify a source, or approve the
    repair. The returned schedule must still be materialized and passed to
    ``check_validity`` by the parent workflow.
    """
    try:
        report = _unwrap_report(disruption_report)
        if not isinstance(schedule_rows, list) or not schedule_rows:
            raise RepairInputError("schedule_rows must be a nonempty list.")
        if any(not isinstance(row, dict) for row in schedule_rows):
            raise RepairInputError("Every schedule_rows item must be an object.")
        if not isinstance(repair_assignments, list):
            raise RepairInputError("repair_assignments must be a list.")
        if session_id_field is not None and not str(session_id_field).strip():
            raise RepairInputError("session_id_field cannot be blank.")
        exact_id_field = str(session_id_field).strip() if session_id_field else None
        affected = _clean_affected_keys(affected_session_keys)
        affected_set = {_normalise_identity(value) for value in affected}

        row_index: dict[str, int] = {}
        for index, row in enumerate(schedule_rows):
            key = _session_key(row, exact_id_field)
            if not key:
                raise RepairInputError(
                    f"schedule_rows[{index}] has no stable session identifier."
                )
            normalised = _normalise_identity(key)
            if normalised in row_index:
                raise RepairInputError(f"Duplicate schedule session ID: {key!r}.")
            row_index[normalised] = index
        missing_rows = [key for key in affected if _normalise_identity(key) not in row_index]
        if missing_rows:
            raise RepairInputError(
                "Affected sessions are missing from schedule_rows: " + ", ".join(missing_rows)
            )

        assignments = [
            _normalise_assignment(record, index)
            for index, record in enumerate(repair_assignments)
        ]
        assignment_map: dict[str, dict[str, Any]] = {}
        for assignment in assignments:
            key = _normalise_identity(assignment["session_key"])
            if key in assignment_map:
                raise RepairInputError(
                    f"Duplicate repair assignment for {assignment['session_key']!r}."
                )
            assignment_map[key] = assignment
        assignment_set = set(assignment_map)
        if assignment_set != affected_set:
            missing = sorted(affected_set - assignment_set)
            unexpected = sorted(assignment_set - affected_set)
            details = []
            if missing:
                details.append("missing assignments: " + ", ".join(missing))
            if unexpected:
                details.append("assignments outside the repair scope: " + ", ".join(unexpected))
            raise RepairInputError("repair_assignments must exactly match the affected scope (" + "; ".join(details) + ").")

        modified_rows = copy.deepcopy(schedule_rows)
        changes: list[dict[str, Any]] = []
        for supplied_key in affected:
            normalised = _normalise_identity(supplied_key)
            assignment = assignment_map[normalised]
            _validate_assignment_against_report(report, assignment)
            row = modified_rows[row_index[normalised]]
            before, after = _apply_assignment(row, assignment, supplied_key)
            changes.append(
                {
                    "session_key": supplied_key,
                    "before": before,
                    "after": after,
                }
            )
    except RepairInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "repair_complete": False,
                "summary": str(exc),
                "modified_schedule": None,
            }
        )

    return _json(
        {
            "status": "success",
            "repair_complete": True,
            "summary": (
                f"One in-memory modified schedule was produced for "
                f"{len(changes)} affected session(s)."
            ),
            "disruption_id": report["disruption_id"],
            "modified_schedule": {
                "format": "schedule_rows",
                "row_count": len(modified_rows),
                "rows": modified_rows,
            },
            "changes": changes,
            "side_effects": {
                "source_modified": False,
                "file_written": False,
                "repair_approved": False,
            },
            "validation_handoff": {
                "validation_run": False,
                "ready_for_approval": False,
                "required_next_action": (
                    "The parent workflow must materialize this exact schedule and run check_validity."
                ),
            },
        }
    )
