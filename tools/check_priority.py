"""Deterministic academic and operational priority ranking for affected sessions."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from langchain.tools import tool

from .find_affected_sessions import find_affected_sessions


FIND_PAGE_SIZE = 100
MAX_FIND_PAGES = 100
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 100

# Confirmed university repair hierarchy. Bonuses are deliberately capped below
# the 100-point tier gap, so a lower tier can never overtake a higher tier.
PRIORITY_POLICY: dict[str, dict[str, Any]] = {
    "exam_or_quiz": {
        "tier": 4,
        "level": "critical",
        "base_score": 400,
        "label": "Exam or Quiz",
        "policy_reason": (
            "Exams and quizzes have the highest repair priority because assessment "
            "timing and common student availability are highly constrained."
        ),
    },
    "lecture": {
        "tier": 3,
        "level": "high",
        "base_score": 300,
        "label": "Lecture",
        "policy_reason": (
            "Lectures are ranked after assessments because every assigned student "
            "group must be available together in the replacement slot."
        ),
    },
    "laboratory": {
        "tier": 2,
        "level": "medium",
        "base_score": 200,
        "label": "Laboratory",
        "policy_reason": (
            "Laboratories are ranked after lectures because a replacement requires "
            "suitable equipment, room type, capacity, and staff availability."
        ),
    },
    "tutorial": {
        "tier": 1,
        "level": "low",
        "base_score": 100,
        "label": "Tutorial",
        "policy_reason": (
            "Tutorials are the most flexible session type, but every student, staff, "
            "room, and validity constraint still remains mandatory."
        ),
    },
}

PRIORITY_COLORS = {
    "critical": {
        "label": "Critical",
        "color_name": "red",
        "foreground": "#991B1B",
        "background": "#FEE2E2",
        "border": "#EF4444",
        "symbol": "P1",
    },
    "high": {
        "label": "High",
        "color_name": "amber",
        "foreground": "#92400E",
        "background": "#FEF3C7",
        "border": "#F59E0B",
        "symbol": "P2",
    },
    "medium": {
        "label": "Medium",
        "color_name": "blue",
        "foreground": "#1E40AF",
        "background": "#DBEAFE",
        "border": "#60A5FA",
        "symbol": "P3",
    },
    "low": {
        "label": "Low",
        "color_name": "gray",
        "foreground": "#475569",
        "background": "#F1F5F9",
        "border": "#94A3B8",
        "symbol": "P4",
    },
    "unclassified": {
        "label": "Clarification Required",
        "color_name": "purple",
        "foreground": "#6B21A8",
        "background": "#F3E8FF",
        "border": "#C084FC",
        "symbol": "?",
    },
}

FIELD_ALIASES: dict[str, set[str]] = {
    "session_key": {
        "affectedsessionkey",
        "sessionid",
        "examid",
        "assessmentid",
        "eventid",
        "bookingid",
    },
    "session_type": {
        "sessiontype",
        "assessmenttype",
        "examtype",
        "activitytype",
        "classtype",
        "type",
    },
    "course_id": {"courseid", "coursecode", "moduleid", "modulecode"},
    "course_name": {"coursename", "modulename", "subject", "subjectname"},
    "expected_students": {
        "expectedstudents",
        "students",
        "studentcount",
        "enrollment",
        "enrolment",
        "candidates",
        "minimumroomcapacity",
    },
    "student_groups": {
        "studentgroups",
        "cohortgroups",
        "tutorialgroups",
        "groupid",
    },
    "duration_minutes": {"durationminutes"},
    "duration_periods": {"durationperiods"},
    "room": {"room", "originalroom", "roomid", "venue", "location"},
    "room_type": {"roomtype", "requiredroomtype", "venuetype"},
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
    "instructor": {
        "instructor",
        "lecturer",
        "doctor",
        "staff",
    },
    "teaching_assistant": {"ta", "teachingassistant"},
    "priority": {"priority", "prioritylevel", "academicpriority"},
    "previously_rescheduled": {
        "previouslyrescheduled",
        "rescheduledbefore",
        "repaircount",
    },
    "fixed_constraint": {
        "fixed",
        "fixedslot",
        "fixeddate",
        "immovable",
        "limitedavailability",
    },
}


class PriorityInputError(ValueError):
    """Raised when priority input is unsafe or incomplete."""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalise_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    dictionaries: list[dict[str, Any]] = []
    if isinstance(value, dict):
        dictionaries.append(value)
        for nested_key in (
            "compensation_requirements",
            "additional_source_fields",
            "requirements",
            "required_staff",
            "details",
        ):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                dictionaries.extend(_walk_dicts(nested))
    return dictionaries


def _value(session: dict[str, Any], field: str) -> Any:
    aliases = FIELD_ALIASES[field]
    for mapping in _walk_dicts(session):
        for key, value in mapping.items():
            if _normalise_key(key) in aliases and value not in (None, "", {}, []):
                return value
    return None


def _number(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, number)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return _normalise_text(value) in {
        "true",
        "yes",
        "required",
        "fixed",
        "immovable",
        "limited",
    }


def _split_groups(value: Any) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[;,|\n]+", str(value or ""))
        if item.strip()
    ]


def _classify(session_type: Any, session_key: str) -> tuple[str | None, str]:
    text = _normalise_text(session_type)
    compact = _normalise_key(text)
    if re.search(r"\b(exam|quiz|midterm)\b", text) or any(
        term in compact for term in ("finalexam", "practicalquiz", "writtenquiz")
    ):
        return "exam_or_quiz", "classified from session_type"
    if "lecture" in text:
        return "lecture", "classified from session_type"
    if "laboratory" in text or re.search(r"\blab\b", text):
        return "laboratory", "classified from session_type"
    if "tutorial" in text:
        return "tutorial", "classified from session_type"

    # Stable prefixes are used only when the type field is absent, never to
    # override an explicit but unfamiliar session type.
    if not text:
        prefix = session_key.strip().casefold()
        if prefix.startswith(("exm-", "exam-", "quiz-", "final-")):
            return "exam_or_quiz", "inferred from stable session-ID prefix"
        if prefix.startswith("lec-"):
            return "lecture", "inferred from stable session-ID prefix"
        if prefix.startswith("lab-"):
            return "laboratory", "inferred from stable session-ID prefix"
        if prefix.startswith(("tut-", "tutorial-")):
            return "tutorial", "inferred from stable session-ID prefix"
    return None, "session type does not match the confirmed priority policy"


def _tie_breaker_score(session: dict[str, Any]) -> tuple[int, dict[str, int], dict[str, Any]]:
    expected_students = _number(_value(session, "expected_students"))
    groups = _split_groups(_value(session, "student_groups"))
    duration_minutes = _number(_value(session, "duration_minutes"))
    duration_periods = _number(_value(session, "duration_periods"))
    has_special_resource = any(
        _value(session, field) not in (None, "", {}, [])
        for field in ("room_type", "equipment", "accessibility")
    )
    fixed_constraint = _truthy(_value(session, "fixed_constraint"))
    reschedule_value = _value(session, "previously_rescheduled")
    previously_rescheduled = _truthy(reschedule_value) or (_number(reschedule_value) or 0) > 0

    attendance_bonus = min(35, math.ceil((expected_students or 0) / 25))
    group_bonus = min(25, len(groups) * 2)
    resource_bonus = 10 if has_special_resource else 0
    fixed_bonus = 10 if fixed_constraint else 0
    fairness_bonus = 5 if previously_rescheduled else 0
    duration_basis = duration_minutes or ((duration_periods or 0) * 90)
    duration_bonus = min(10, math.ceil(duration_basis / 90)) if duration_basis else 0

    breakdown = {
        "attendance_impact": attendance_bonus,
        "student_group_impact": group_bonus,
        "special_resource_complexity": resource_bonus,
        "fixed_constraint": fixed_bonus,
        "previous_reschedule_fairness": fairness_bonus,
        "duration_complexity": duration_bonus,
    }
    evidence = {
        "expected_students": expected_students,
        "student_group_count": len(groups),
        "student_groups": groups,
        "duration_minutes": duration_minutes,
        "duration_periods": duration_periods,
        "special_resource_requirement_present": has_special_resource,
        "fixed_constraint_present": fixed_constraint,
        "previously_rescheduled": previously_rescheduled,
    }
    return sum(breakdown.values()), breakdown, evidence


def _repair_requirements(
    classification: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    common = [
        "Preserve the original duration and academic requirements.",
        "Reject every staff, student, room, equipment, and accessibility conflict.",
    ]
    required_checks = ["check_lecturer_or_ta_availability", "check_room_availability"]
    if classification == "exam_or_quiz":
        common.insert(
            0,
            "Treat the assessment window and simultaneous student availability as highly constrained.",
        )
    elif classification == "lecture":
        common.insert(0, "Find one common slot for every assigned lecture group.")
    elif classification == "laboratory":
        common.insert(
            0,
            "Confirm specialized room, equipment, capacity, and staff availability together.",
        )
    else:
        common.insert(
            0,
            "Use tutorial flexibility only after all mandatory availability checks pass.",
        )

    return {
        "constraints_to_preserve": common,
        "required_checks": required_checks,
        "original_room": _value(session, "room"),
        "required_room_type": _value(session, "room_type"),
        "required_equipment": _value(session, "equipment"),
        "accessibility_requirements": _value(session, "accessibility"),
    }


def _rank_session(session: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    session_key_value = _value(session, "session_key")
    session_key = str(session_key_value).strip() if session_key_value not in (None, "") else ""
    if not session_key:
        return None, {
            "session_key": None,
            "session_type": _value(session, "session_type"),
            "reason": "A stable affected_session_key or session ID is required.",
            "source": session.get("source"),
            "display": PRIORITY_COLORS["unclassified"],
        }

    session_type = _value(session, "session_type")
    classification, classification_source = _classify(session_type, session_key)
    if classification is None:
        return None, {
            "session_key": session_key,
            "session_type": session_type,
            "course_id": _value(session, "course_id"),
            "reason": (
                "The session type is outside the confirmed hierarchy. Specify whether it "
                "should rank with exams/quizzes, lectures, laboratories, or tutorials."
            ),
            "classification_evidence": classification_source,
            "source": session.get("source"),
            "display": PRIORITY_COLORS["unclassified"],
        }

    policy = PRIORITY_POLICY[classification]
    tie_score, score_breakdown, evidence = _tie_breaker_score(session)
    score = policy["base_score"] + tie_score
    reasons = [policy["policy_reason"]]
    if evidence["expected_students"]:
        reasons.append(
            f"The session has {evidence['expected_students']} expected students."
        )
    if evidence["student_group_count"]:
        reasons.append(
            f"The session affects {evidence['student_group_count']} student group(s)."
        )
    if evidence["special_resource_requirement_present"]:
        reasons.append("The source record contains a specialized resource requirement.")
    if evidence["fixed_constraint_present"]:
        reasons.append("The source record explicitly marks a fixed or limited constraint.")
    if evidence["previously_rescheduled"]:
        reasons.append("Fairness priority increased because the session was rescheduled before.")

    return {
        "session_key": session_key,
        "course_id": _value(session, "course_id"),
        "course_name": _value(session, "course_name"),
        "session_type": session_type,
        "classification": classification,
        "classification_label": policy["label"],
        "classification_evidence": classification_source,
        "priority_tier": policy["tier"],
        "priority_level": policy["level"],
        "priority_score": score,
        "score_breakdown": {
            "policy_base": policy["base_score"],
            **score_breakdown,
            "total": score,
        },
        "reasons": reasons,
        "tie_breaker_evidence": evidence,
        "repair_requirements": _repair_requirements(classification, session),
        "original_source": session.get("source"),
        "display": PRIORITY_COLORS[policy["level"]],
    }, None


def _impact_summary(ranked: list[dict[str, Any]], unclassified: list[dict[str, Any]]) -> dict[str, Any]:
    levels = Counter(item["priority_level"] for item in ranked)
    classifications = Counter(item["classification"] for item in ranked)
    expected_attendance = sum(
        item["tie_breaker_evidence"].get("expected_students") or 0
        for item in ranked
    )
    groups = {
        group
        for item in ranked
        for group in item["tie_breaker_evidence"].get("student_groups", [])
    }
    return {
        "ranked_session_count": len(ranked),
        "unclassified_session_count": len(unclassified),
        "sessions_by_priority_level": dict(sorted(levels.items())),
        "sessions_by_policy_class": dict(sorted(classifications.items())),
        "sum_expected_attendance": expected_attendance,
        "attendance_note": (
            "This is a sum of expected attendance per session, not a deduplicated student count."
        ),
        "unique_student_group_count": len(groups),
    }


def _decode_find_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "content"):
        raw_result = raw_result.content
    if isinstance(raw_result, dict):
        payload = raw_result
    elif isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise PriorityInputError(
                f"find_affected_sessions returned invalid JSON: {exc}"
            ) from exc
    else:
        raise PriorityInputError(
            "find_affected_sessions returned an unsupported result type."
        )
    if not isinstance(payload, dict):
        raise PriorityInputError(
            "find_affected_sessions did not return a structured JSON object."
        )
    return payload


def _load_complete_affected_scope(
    uploaded_file_path: str,
    affected_day_or_date: str,
    academic_week: int,
    sheet_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    offset = 0
    expected_total: int | None = None

    for page_number in range(1, MAX_FIND_PAGES + 1):
        try:
            raw_result = find_affected_sessions.invoke(
                {
                    "uploaded_file_path": uploaded_file_path,
                    "affected_day_or_date": affected_day_or_date,
                    "academic_week": academic_week,
                    "sheet_name": sheet_name,
                    "result_offset": offset,
                    "result_limit": FIND_PAGE_SIZE,
                }
            )
        except Exception as exc:
            raise PriorityInputError(
                f"find_affected_sessions could not be invoked: {type(exc).__name__}: {exc}"
            ) from exc

        payload = _decode_find_result(raw_result)
        if payload.get("status") != "success" or payload.get("complete") is not True:
            raise PriorityInputError(
                "The complete affected-session scope could not be retrieved. "
                f"Dependency response: {payload.get('summary') or payload.get('reason')}"
            )

        total = payload.get("affected_session_count")
        if not isinstance(total, int) or total < 0:
            raise PriorityInputError(
                "find_affected_sessions omitted a valid affected_session_count."
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise PriorityInputError(
                "The affected-session count changed during priority retrieval."
            )

        page_sessions = payload.get("affected_sessions")
        if not isinstance(page_sessions, list) or not all(
            isinstance(item, dict) for item in page_sessions
        ):
            raise PriorityInputError(
                "find_affected_sessions returned an invalid affected_sessions page."
            )
        for session in page_sessions:
            key_value = _value(session, "session_key")
            key = str(key_value).strip() if key_value not in (None, "") else ""
            if not key:
                raise PriorityInputError(
                    "An affected session is missing a stable affected_session_key."
                )
            if key in seen_keys:
                raise PriorityInputError(
                    f"Duplicate affected_session_key returned across pages: {key}."
                )
            seen_keys.add(key)
            sessions.append(session)

        pagination = payload.get("result_pagination")
        if not isinstance(pagination, dict):
            raise PriorityInputError(
                "find_affected_sessions omitted result pagination metadata."
            )
        has_more = pagination.get("has_more")
        if has_more is False:
            if len(sessions) != expected_total:
                raise PriorityInputError(
                    "The retrieved affected-session total does not match the verified scope."
                )
            return sessions, {
                "affected_session_count": expected_total,
                "find_pages_retrieved": page_number,
                "unique_session_keys": len(seen_keys),
                "complete": True,
            }
        if has_more is not True:
            raise PriorityInputError(
                "find_affected_sessions did not state whether more records exist."
            )
        next_offset = pagination.get("next_result_offset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise PriorityInputError(
                "find_affected_sessions returned an invalid next_result_offset."
            )
        offset = next_offset

    raise PriorityInputError(
        f"Affected-session retrieval exceeded {MAX_FIND_PAGES} pages."
    )


@tool
def check_priority(
    uploaded_file_path: str,
    affected_day_or_date: str,
    academic_week: int,
    sheet_name: str = "Semester Timetable",
    disruption_details: str | None = None,
    result_offset: int = 0,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> str:
    """Rank affected sessions using the confirmed university repair hierarchy.

    The strict order is exam/quiz, lecture, laboratory, then tutorial. Enrollment,
    group count, duration, fixed constraints, resource complexity, and previous
    rescheduling only break ties inside the same tier. The tool retrieves every
    detailed page from ``find_affected_sessions``, calculates one global repair
    order for the entire affected day, and paginates only the detailed ranking
    output. It does not check availability, move sessions, relax constraints,
    validate a repair, or approve a change.

    Unknown session types are never guessed; they are returned through
    ``unclassified_sessions`` for user clarification.
    """
    request = {
        "uploaded_file_path": uploaded_file_path,
        "affected_day_or_date": affected_day_or_date,
        "academic_week": academic_week,
        "sheet_name": sheet_name,
        "disruption_details": disruption_details,
        "result_offset": result_offset,
        "result_limit": result_limit,
    }
    try:
        if not str(uploaded_file_path).strip():
            raise PriorityInputError("uploaded_file_path is required.")
        if not str(affected_day_or_date).strip():
            raise PriorityInputError("affected_day_or_date is required.")
        if not str(sheet_name).strip():
            raise PriorityInputError("sheet_name is required.")
        if not isinstance(academic_week, int) or isinstance(academic_week, bool) or academic_week < 1:
            raise PriorityInputError("academic_week must be a positive integer.")
        if not isinstance(result_offset, int) or isinstance(result_offset, bool) or result_offset < 0:
            raise PriorityInputError("result_offset must be a non-negative integer.")
        if (
            not isinstance(result_limit, int)
            or isinstance(result_limit, bool)
            or not 1 <= result_limit <= MAX_RESULT_LIMIT
        ):
            raise PriorityInputError(
                f"result_limit must be between 1 and {MAX_RESULT_LIMIT}."
            )
    except PriorityInputError as exc:
        return _json(
            {
                "status": "invalid_request",
                "summary": str(exc),
                "request": request,
                "ranking_complete": False,
                "ranked_sessions": [],
                "unclassified_sessions": [],
            }
        )

    try:
        affected_sessions, retrieval = _load_complete_affected_scope(
            uploaded_file_path=str(uploaded_file_path).strip(),
            affected_day_or_date=str(affected_day_or_date).strip(),
            academic_week=academic_week,
            sheet_name=str(sheet_name).strip(),
        )
    except PriorityInputError as exc:
        return _json(
            {
                "status": "information_required",
                "summary": "The full affected-day priority scope could not be verified.",
                "request": request,
                "ranking_complete": False,
                "ranked_sessions": [],
                "unclassified_sessions": [],
                "reason": str(exc),
                "required_action": (
                    "Correct the affected-session retrieval issue before schedule repair."
                ),
            }
        )

    ranked: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    duplicate_keys: set[str] = set()
    for session in affected_sessions:
        ranking, unresolved = _rank_session(session)
        item = ranking or unresolved
        assert item is not None
        key = item.get("session_key")
        if key and key in seen_keys:
            duplicate_keys.add(key)
        elif key:
            seen_keys.add(key)
        if ranking:
            ranked.append(ranking)
        else:
            unclassified.append(unresolved or {})

    if duplicate_keys:
        return _json(
            {
                "status": "information_required",
                "summary": "Duplicate session keys prevent a reliable priority order.",
                "request": request,
                "ranking_complete": False,
                "duplicate_session_keys": sorted(duplicate_keys),
                "ranked_sessions": [],
                "unclassified_sessions": unclassified,
                "required_action": (
                    "Correct duplicate affected_session_key values before schedule repair."
                ),
            }
        )

    ranked.sort(
        key=lambda item: (
            -item["priority_tier"],
            -item["priority_score"],
            item["session_key"].casefold(),
        )
    )
    for position, item in enumerate(ranked, start=1):
        item["repair_order"] = position

    complete = not unclassified
    impact = _impact_summary(ranked, unclassified)
    if result_offset > len(ranked):
        return _json(
            {
                "status": "invalid_request",
                "summary": "result_offset is beyond the ranked-session result set.",
                "request": request,
                "ranking_complete": complete,
                "ranked_session_count": len(ranked),
                "ranked_sessions": [],
                "unclassified_sessions": unclassified,
            }
        )

    page_end = min(result_offset + result_limit, len(ranked))
    ranked_page = ranked[result_offset:page_end]
    has_more_results = page_end < len(ranked)
    if complete:
        status = "success"
        summary = (
            f"{len(ranked)} affected session(s) were ranked using the confirmed "
            "exam/quiz > lecture > laboratory > tutorial policy. "
            f"Detailed global ranks {result_offset + 1 if ranked_page else 0}-{page_end} "
            "are returned."
        )
    else:
        status = "information_required"
        summary = (
            f"{len(ranked)} session(s) were ranked, but {len(unclassified)} session(s) "
            "require a confirmed priority classification."
        )

    response: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "ranking_complete": complete,
        "policy": {
            "confirmed": True,
            "strict_hierarchy": [
                "exam_or_quiz",
                "lecture",
                "laboratory",
                "tutorial",
            ],
            "hierarchy_display": (
                "Exam or Quiz > Lecture > Laboratory > Tutorial"
            ),
            "tie_breaker_rule": (
                "Impact and complexity bonuses apply only within a tier and can never "
                "move a lower session type above a higher one."
            ),
            "constraint_rule": (
                "Lower priority never permits a conflict or relaxation of a hard constraint."
            ),
        },
        "request": request,
        "retrieval": retrieval,
        "impact_summary": impact,
        "ranked_session_count": len(ranked),
        "returned_ranked_session_count": len(ranked_page),
        "global_repair_order": [item["session_key"] for item in ranked],
        "ranked_sessions": ranked_page,
        "unclassified_sessions": unclassified,
        "result_pagination": {
            "result_offset": result_offset,
            "result_limit": result_limit,
            "returned_range": (
                [result_offset + 1, page_end]
                if ranked_page
                else []
            ),
            "has_more": has_more_results,
            "next_result_offset": page_end if has_more_results else None,
            "page_is_final": not has_more_results,
            "instruction": (
                "Call check_priority again with result_offset set to "
                "next_result_offset until has_more is false."
                if has_more_results
                else "This is the final detailed global-priority page."
            ),
        },
        "ui_presentation": {
            "title": "Affected Session Priority Order",
            "priority_legend": [
                PRIORITY_COLORS[level]
                for level in ("critical", "high", "medium", "low", "unclassified")
            ],
            "accessibility_note": (
                "Every priority color is paired with a text label and P-level symbol."
            ),
        },
        "next_action": (
            "Use this order to generate candidates, then run staff availability, room "
            "availability, schedule repair, and final validity checks."
            if complete
            else "Obtain a policy classification for every unclassified session first."
        ),
    }
    if unclassified:
        response["required_action"] = (
            "Classify each unknown session type as exam/quiz, lecture, laboratory, or tutorial."
        )
    return _json(response)
