"""Validate and normalize university scheduling disruption reports.

This tool is the formal intake boundary for the repair workflow.  It does not
alter a timetable or 1claim that reported facts have been verified against an
uploaded schedule.  Instead, it produces a deterministic, structured report
that downstream retrieval, repair, comparison, and validation tools can use.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from langchain.tools import tool


WEEKDAY_ALIASES = {
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

DISRUPTION_TYPES: dict[str, dict[str, Any]] = {
    "day_cancelled": {
        "label": "University day cancelled",
        "aliases": {
            "cancel day",
            "cancelled day",
            "canceled day",
            "day cancellation",
            "day cancelled",
            "day canceled",
            "university closure",
        },
        "required": {"day", "week"},
        "default_urgency": "critical",
        "next_tool": "find_affected_sessions",
    },
    "partial_day_cancelled": {
        "label": "University partial day or time block cancelled",
        "aliases": {
            "partial day cancelled",
            "partial day canceled",
            "partial day cancellation",
            "time block cancelled",
            "time block canceled",
            "period cancelled",
            "period canceled",
            "campus closed for part of day",
        },
        "required": {"day", "week", "time_scope"},
        "default_urgency": "critical",
        "next_tool": "get_schedule",
    },
    "lecturer_or_ta_unavailable": {
        "label": "Lecturer or teaching assistant unavailable",
        "aliases": {
            "lecturer unavailable",
            "doctor unavailable",
            "instructor unavailable",
            "ta unavailable",
            "teaching assistant unavailable",
            "staff unavailable",
            "lecturer or ta unavailable",
        },
        "required": {"day", "week", "resources", "time_scope"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "room_closed": {
        "label": "Room or laboratory closed",
        "aliases": {
            "room closed",
            "room unavailable",
            "lab closed",
            "laboratory closed",
            "venue closed",
            "venue unavailable",
        },
        "required": {"day", "week", "resources", "time_scope"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "equipment_unavailable": {
        "label": "Required equipment unavailable",
        "aliases": {
            "equipment unavailable",
            "equipment failure",
            "equipment broken",
            "resource unavailable",
        },
        "required": {"day", "week", "resources", "time_scope"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "session_cancelled": {
        "label": "Specific session cancelled",
        "aliases": {
            "session cancelled",
            "session canceled",
            "class cancelled",
            "class canceled",
            "lecture cancelled",
            "tutorial cancelled",
            "lab cancelled",
        },
        "required": {"sessions"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "visiting_professor_limited": {
        "label": "Visiting professor availability changed",
        "aliases": {
            "visiting professor limited",
            "visiting professor availability",
            "visiting professor unavailable",
            "guest professor limited",
        },
        "required": {"day", "week", "resources", "time_scope"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "unexpected_exam": {
        "label": "Unexpected examination or quiz added",
        "aliases": {
            "unexpected exam",
            "exam added",
            "added exam",
            "new exam",
            "unexpected quiz",
            "quiz added",
            "added quiz",
        },
        "required": {"day", "week", "time_scope", "student_groups"},
        "default_urgency": "critical",
        "next_tool": "get_schedule",
    },
    "room_capacity_corrected": {
        "label": "Room capacity corrected",
        "aliases": {
            "room capacity corrected",
            "capacity corrected",
            "room capacity changed",
            "capacity changed",
        },
        "required": {"resources", "capacity"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "university_event": {
        "label": "University event occupies scheduling resources",
        "aliases": {
            "university event",
            "campus event",
            "event room takeover",
            "rooms reserved for event",
        },
        "required": {"day", "week", "resources", "time_scope"},
        "default_urgency": "high",
        "next_tool": "get_schedule",
    },
    "repair_rejected": {
        "label": "Proposed schedule repair rejected",
        "aliases": {
            "repair rejected",
            "schedule change rejected",
            "department rejected repair",
            "department rejected change",
        },
        "required": {"repair"},
        "default_urgency": "high",
        "next_tool": "run_schedule_repair",
    },
}

URGENCY_ALIASES = {
    "critical": "critical",
    "urgent": "critical",
    "emergency": "critical",
    "high": "high",
    "normal": "normal",
    "medium": "normal",
    "low": "low",
}

URGENCY_DISPLAY = {
    "critical": {"label": "Critical", "symbol": "P1", "color": "#DC2626"},
    "high": {"label": "High", "symbol": "P2", "color": "#F59E0B"},
    "normal": {"label": "Normal", "symbol": "P3", "color": "#2563EB"},
    "low": {"label": "Low", "symbol": "P4", "color": "#64748B"},
}


class DisruptionInputError(ValueError):
    """Raised when supplied disruption data is malformed or contradictory."""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_phrase(value: Any) -> str:
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split()
    )


def _normalise_disruption_type(value: str) -> str | None:
    phrase = _normalise_phrase(value)
    if not phrase:
        return None
    for canonical, policy in DISRUPTION_TYPES.items():
        accepted = {_normalise_phrase(canonical), *policy["aliases"]}
        if phrase in {_normalise_phrase(item) for item in accepted}:
            return canonical
    return None


def _normalise_identifiers(values: list[str] | None, field: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise DisruptionInputError(f"{field} must be a list of identifiers.")

    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise DisruptionInputError(
                f"{field}[{index}] must be a nonempty string identifier."
            )
        cleaned = " ".join(value.strip().split())
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _parse_day_or_date(value: str | None) -> tuple[str | None, str | None, str | None]:
    """Return normalized weekday, ISO date, and source precision."""
    if value is None or not str(value).strip():
        return None, None, None

    text = str(value).strip()
    weekday = WEEKDAY_ALIASES.get(text.casefold().rstrip("."))
    if weekday:
        return weekday, None, "weekday"

    try:
        exact_date = date.fromisoformat(text)
    except ValueError as exc:
        raise DisruptionInputError(
            "affected_day_or_date must be a weekday or an ISO date in YYYY-MM-DD format."
        ) from exc
    return exact_date.strftime("%A"), exact_date.isoformat(), "exact_date"


def _parse_time(value: str | None, field: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%H:%M")
        except ValueError:
            continue
    raise DisruptionInputError(f"{field} must use 24-hour HH:MM format.")


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _stable_disruption_id(report_data: dict[str, Any]) -> str:
    canonical = json.dumps(
        report_data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12].upper()
    return f"DSP-{digest}"


def _missing_fields(
    disruption_type: str,
    *,
    day: str | None,
    academic_week: int | None,
    whole_day: bool,
    start_time: str | None,
    end_time: str | None,
    resources: list[str],
    sessions: list[str],
    student_groups: list[str],
    corrected_room_capacity: int | None,
    related_repair_id: str | None,
) -> list[dict[str, str]]:
    requirements = DISRUPTION_TYPES[disruption_type]["required"]
    missing: list[dict[str, str]] = []

    checks = {
        "day": (
            bool(day),
            "affected_day_or_date",
            "Provide the affected weekday or exact date (YYYY-MM-DD).",
        ),
        "week": (
            academic_week is not None,
            "academic_week",
            "Provide the academic week number from 1 to 53.",
        ),
        "resources": (
            bool(resources),
            "affected_resource_ids",
            "Provide every affected staff, room, equipment, or resource identifier.",
        ),
        "sessions": (
            bool(sessions),
            "affected_session_ids",
            "Provide every cancelled session identifier.",
        ),
        "student_groups": (
            bool(student_groups),
            "affected_student_group_ids",
            "Provide every student group affected by the added assessment.",
        ),
        "capacity": (
            corrected_room_capacity is not None,
            "corrected_room_capacity",
            "Provide the confirmed corrected room capacity.",
        ),
        "repair": (
            bool(related_repair_id),
            "related_repair_id",
            "Provide the identifier of the rejected repair.",
        ),
        "time_scope": (
            whole_day or bool(start_time and end_time),
            "whole_day/start_time/end_time",
            "Set whole_day=true or provide both start_time and end_time.",
        ),
    }
    for requirement in requirements:
        satisfied, field, request = checks[requirement]
        if not satisfied:
            missing.append({"field": field, "request": request})
    return sorted(missing, key=lambda item: item["field"])


def _next_action(
    disruption_type: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    next_tool = DISRUPTION_TYPES[disruption_type]["next_tool"]
    if next_tool == "find_affected_sessions":
        arguments: dict[str, Any] = {
            "affected_day_or_date": report["scope"].get("affected_date")
            or report["scope"].get("affected_day"),
            "academic_week": report["scope"].get("academic_week"),
        }
        return {
            "tool": next_tool,
            "purpose": "Retrieve the complete schedule scope affected by this disruption.",
            "arguments_from_report": arguments,
            "additional_required_argument": "uploaded_file_path",
            "verification_rule": (
                "Retrieve all pages and confirm complete=true before ranking or repair."
            ),
        }
    if next_tool == "get_schedule":
        scope = report["scope"]
        suggested_filters: dict[str, Any] = {}
        if scope.get("affected_day"):
            suggested_filters["day"] = scope["affected_day"]
        if scope.get("academic_week") is not None:
            suggested_filters["week"] = str(scope["academic_week"])

        disruption_type = report["disruption_type"]
        resources = report["affected_resource_ids"]
        if disruption_type in {
            "lecturer_or_ta_unavailable",
            "visiting_professor_limited",
        } and resources:
            suggested_filters["instructor"] = resources
        elif disruption_type in {
            "room_closed",
            "room_capacity_corrected",
            "university_event",
        } and resources:
            suggested_filters["room"] = resources
        elif disruption_type == "unexpected_exam" and report["affected_student_group_ids"]:
            suggested_filters["student_groups"] = report["affected_student_group_ids"]

        query_identifiers = (
            report["affected_session_ids"]
            if report["affected_session_ids"]
            else resources
            if disruption_type == "equipment_unavailable"
            else []
        )
        return {
            "tool": next_tool,
            "purpose": (
                "Retrieve every schedule row matching the reported resources, sessions, "
                "student groups, date, week, and time scope."
            ),
            "suggested_filters": suggested_filters,
            "query_identifiers": query_identifiers,
            "additional_required_argument": "uploaded_file_path",
            "time_scope": {
                "whole_day": scope.get("whole_day"),
                "start_time": scope.get("start_time"),
                "end_time": scope.get("end_time"),
            },
            "verification_rule": (
                "Retrieve all result pages, then retain only rows whose fields and "
                "time interval satisfy the complete disruption scope."
            ),
        }
    return {
        "tool": next_tool,
        "purpose": "Generate a replacement for the rejected repair.",
        "arguments_from_report": {
            "disruption_id": report["disruption_id"],
            "related_repair_id": report.get("related_repair_id"),
        },
    }


@tool
def report_disruption(
    disruption_type: str,
    description: str,
    affected_day_or_date: str | None = None,
    academic_week: int | None = None,
    affected_resource_ids: list[str] | None = None,
    affected_session_ids: list[str] | None = None,
    affected_student_group_ids: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    whole_day: bool = False,
    urgency: str | None = None,
    hard_constraints: list[str] | None = None,
    evidence_references: list[str] | None = None,
    corrected_room_capacity: int | None = None,
    related_repair_id: str | None = None,
    reported_by: str | None = None,
) -> str:
    """Create a validated disruption report before any schedule repair.

    Use this tool for full-day or partial-day cancellations, unavailable
    staff/rooms/equipment, cancelled sessions, visiting-professor restrictions,
    added exams or quizzes, room-capacity corrections, university events, and
    rejected repairs.  The
    result contains a stable disruption ID, normalized scope, missing-information
    requests, hard constraints, and the next workflow action.  It records the
    supplied report only; it does not verify schedule facts or change a schedule.
    Do not proceed to repair unless ``report_complete`` is true. For a partial
    day provide both ``start_time`` and ``end_time``; for a full day set
    ``whole_day=true`` and omit both times. Preserve the returned
    ``disruption_id`` throughout retrieval, repair, comparison, validation, and
    approval.

    ``schedule_facts_verified=false`` means every reported fact still requires
    authoritative schedule retrieval. Follow ``next_action`` and retrieve all
    result pages. For whole-day cancellations use ``find_affected_sessions``;
    for other disruptions follow the targeted retrieval instructions returned
    by this tool.
    """
    raw_request = {
        "disruption_type": disruption_type,
        "affected_day_or_date": affected_day_or_date,
        "academic_week": academic_week,
    }

    try:
        if not isinstance(description, str) or not description.strip():
            raise DisruptionInputError("description is required and must be nonempty.")
        normalized_description = " ".join(description.strip().split())

        normalized_type = _normalise_disruption_type(disruption_type)
        if normalized_type is None:
            return _json(
                {
                    "status": "information_required",
                    "report_complete": False,
                    "summary": "The disruption type is not recognized and cannot be inferred safely.",
                    "request": raw_request,
                    "supported_disruption_types": [
                        {"value": key, "label": value["label"]}
                        for key, value in DISRUPTION_TYPES.items()
                    ],
                    "required_action": (
                        "Select one supported disruption_type through the website request section."
                    ),
                }
            )

        if normalized_type == "day_cancelled" and whole_day is not True:
            raise DisruptionInputError(
                "day_cancelled requires whole_day=true; use partial_day_cancelled "
                "or session_cancelled for a smaller scope."
            )
        if normalized_type == "partial_day_cancelled" and whole_day is True:
            raise DisruptionInputError(
                "partial_day_cancelled requires start_time and end_time; use "
                "day_cancelled for a full teaching day."
            )

        day, exact_date, day_precision = _parse_day_or_date(affected_day_or_date)
        if academic_week is not None:
            if isinstance(academic_week, bool) or not isinstance(academic_week, int):
                raise DisruptionInputError("academic_week must be an integer from 1 to 53.")
            if not 1 <= academic_week <= 53:
                raise DisruptionInputError("academic_week must be between 1 and 53.")

        normalized_start = _parse_time(start_time, "start_time")
        normalized_end = _parse_time(end_time, "end_time")
        if whole_day and (normalized_start or normalized_end):
            raise DisruptionInputError(
                "whole_day cannot be combined with start_time or end_time."
            )
        if bool(normalized_start) != bool(normalized_end):
            raise DisruptionInputError(
                "start_time and end_time must be provided together."
            )
        if normalized_start and normalized_end and _minutes(normalized_end) <= _minutes(normalized_start):
            raise DisruptionInputError(
                "end_time must be later than start_time on the same university day."
            )

        resources = _normalise_identifiers(affected_resource_ids, "affected_resource_ids")
        sessions = _normalise_identifiers(affected_session_ids, "affected_session_ids")
        student_groups = _normalise_identifiers(
            affected_student_group_ids,
            "affected_student_group_ids",
        )
        constraints = _normalise_identifiers(hard_constraints, "hard_constraints")
        evidence = _normalise_identifiers(evidence_references, "evidence_references")

        if corrected_room_capacity is not None:
            if (
                isinstance(corrected_room_capacity, bool)
                or not isinstance(corrected_room_capacity, int)
                or corrected_room_capacity <= 0
            ):
                raise DisruptionInputError(
                    "corrected_room_capacity must be a positive integer."
                )

        normalized_repair_id = (
            " ".join(related_repair_id.strip().split())
            if isinstance(related_repair_id, str) and related_repair_id.strip()
            else None
        )
        normalized_reporter = (
            " ".join(reported_by.strip().split())
            if isinstance(reported_by, str) and reported_by.strip()
            else None
        )

        if urgency is None or not str(urgency).strip():
            normalized_urgency = DISRUPTION_TYPES[normalized_type]["default_urgency"]
            urgency_source = "policy_default"
        else:
            normalized_urgency = URGENCY_ALIASES.get(_normalise_phrase(urgency))
            if normalized_urgency is None:
                raise DisruptionInputError(
                    "urgency must be critical, high, normal, or low."
                )
            urgency_source = "reported"

        missing = _missing_fields(
            normalized_type,
            day=day,
            academic_week=academic_week,
            whole_day=whole_day,
            start_time=normalized_start,
            end_time=normalized_end,
            resources=resources,
            sessions=sessions,
            student_groups=student_groups,
            corrected_room_capacity=corrected_room_capacity,
            related_repair_id=normalized_repair_id,
        )

        report_body: dict[str, Any] = {
            "report_version": "1.0",
            "disruption_type": normalized_type,
            "disruption_label": DISRUPTION_TYPES[normalized_type]["label"],
            "description": normalized_description,
            "scope": {
                "affected_day": day,
                "affected_date": exact_date,
                "day_precision": day_precision,
                "academic_week": academic_week,
                "whole_day": whole_day,
                "start_time": normalized_start,
                "end_time": normalized_end,
            },
            "affected_resource_ids": resources,
            "affected_session_ids": sessions,
            "affected_student_group_ids": student_groups,
            "corrected_room_capacity": corrected_room_capacity,
            "related_repair_id": normalized_repair_id,
            "urgency": {
                "value": normalized_urgency,
                "source": urgency_source,
                **URGENCY_DISPLAY[normalized_urgency],
            },
            "hard_constraints": constraints,
            "evidence_references": evidence,
            "reported_by": normalized_reporter,
        }
        report_id_data = {
            key: value
            for key, value in report_body.items()
            if key not in {"report_version", "reported_by"}
        }
        report_body["disruption_id"] = _stable_disruption_id(report_id_data)

        if missing:
            return _json(
                {
                    "status": "information_required",
                    "report_complete": False,
                    "summary": (
                        f"The {DISRUPTION_TYPES[normalized_type]['label'].casefold()} report "
                        "is incomplete and must not proceed to schedule repair."
                    ),
                    "disruption_report": report_body,
                    "missing_information": missing,
                    "required_action": (
                        "Provide every listed field through the website request section, "
                        "then call report_disruption again."
                    ),
                }
            )

        warnings: list[str] = []
        if not evidence:
            warnings.append(
                "No evidence reference was supplied; downstream tools must verify the report against authoritative schedules."
            )
        if exact_date and academic_week is not None:
            warnings.append(
                "The reported academic week must be cross-checked against the exact calendar date before repair."
            )

        return _json(
            {
                "status": "success",
                "report_complete": True,
                "summary": (
                    f"Disruption {report_body['disruption_id']} was normalized and is ready for downstream verification."
                ),
                "disruption_report": report_body,
                "validation": {
                    "input_structure_valid": True,
                    "schedule_facts_verified": False,
                    "verification_note": (
                        "Reported facts must be confirmed using schedule retrieval and validity tools."
                    ),
                    "warnings": warnings,
                },
                "next_action": _next_action(normalized_type, report_body),
            }
        )
    except DisruptionInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "report_complete": False,
                "summary": "The disruption report contains invalid or contradictory input.",
                "request": raw_request,
                "error": {
                    "code": "invalid_disruption_input",
                    "message": str(exc),
                },
                "required_action": "Correct the reported field and submit the disruption again.",
            }
        )
