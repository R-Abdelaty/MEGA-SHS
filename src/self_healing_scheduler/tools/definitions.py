"""Model-facing tool definitions. Keep schemas stable as handlers evolve."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    brief: str
    input_schema: JsonSchema
    write_operation: bool = False

    def as_model_tool(self) -> dict[str, Any]:
        """Return a common function-tool shape; adapt this in the model client if needed."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.brief,
                "parameters": self.input_schema,
            },
        }


def _object(properties: JsonSchema, required: tuple[str, ...] = ()) -> JsonSchema:
    schema: JsonSchema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


TIME_RANGE = _object(
    {
        "start": {"type": "string", "description": "ISO 8601 date-time, inclusive."},
        "end": {"type": "string", "description": "ISO 8601 date-time, exclusive."},
    },
    ("start", "end"),
)

ID_LIST = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}

SCOPE = _object(
    {
        "time_range": TIME_RANGE,
        "department_ids": ID_LIST,
        "cohort_ids": ID_LIST,
        "session_ids": ID_LIST,
        "resource_ids": ID_LIST,
    }
)

DISRUPTION = _object(
    {
        "disruption_id": {"type": "string"},
        "type": {
            "type": "string",
            "enum": [
                "lecturer_unavailable",
                "room_closed",
                "resource_unavailable",
                "limited_visitor_availability",
                "examination_added",
                "capacity_corrected",
                "university_event",
                "change_rejected",
                "other",
            ],
        },
        "description": {"type": "string"},
        "affected_ids": ID_LIST,
        "effective_time": TIME_RANGE,
        "metadata": {"type": "object"},
    },
    ("disruption_id", "type", "description", "affected_ids"),
)

PATCH = _object(
    {
        "candidate_id": {"type": "string"},
        "changes": {
            "type": "array",
            "items": _object(
                {
                    "session_id": {"type": "string"},
                    "operation": {"type": "string", "enum": ["create", "update", "cancel"]},
                    "new_time": TIME_RANGE,
                    "new_room_id": {"type": ["string", "null"]},
                    "new_lecturer_ids": ID_LIST,
                    "reason": {"type": "string"},
                },
                ("session_id", "operation", "reason"),
            ),
        },
    },
    ("candidate_id", "changes"),
)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="load_schedule",
        brief="Load the authoritative timetable for a bounded scope, including revision identifiers needed for safe repairs.",
        input_schema=_object(
            {
                "scope": SCOPE,
                "include_cancelled": {"type": "boolean", "default": False},
            },
            ("scope",),
        ),
    ),
    ToolSpec(
        name="load_constraints",
        brief="Load hard and soft university policies plus requirements attached to sessions, people, rooms, and equipment.",
        input_schema=_object(
            {"scope": SCOPE, "session_ids": ID_LIST},
            ("scope",),
        ),
    ),
    ToolSpec(
        name="get_resource_availability",
        brief="Check authoritative availability and suitability for lecturers, cohorts, rooms, equipment, and support resources.",
        input_schema=_object(
            {
                "resource_ids": ID_LIST,
                "resource_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["lecturer", "cohort", "room", "equipment", "support"],
                    },
                    "uniqueItems": True,
                },
                "time_range": TIME_RANGE,
                "requirements": {"type": "object"},
            },
            ("time_range",),
        ),
    ),
    ToolSpec(
        name="find_affected_scope",
        brief="Find directly disrupted sessions and the smallest connected dependency scope that may need repair.",
        input_schema=_object(
            {
                "disruption": DISRUPTION,
                "schedule_revision": {"type": "string"},
                "max_dependency_depth": {"type": "integer", "minimum": 0, "maximum": 10, "default": 2},
            },
            ("disruption", "schedule_revision"),
        ),
    ),
    ToolSpec(
        name="generate_repair_candidates",
        brief="Generate ranked local schedule patches while freezing sessions outside the approved repair scope.",
        input_schema=_object(
            {
                "disruption": DISRUPTION,
                "affected_session_ids": ID_LIST,
                "frozen_session_ids": ID_LIST,
                "schedule_revision": {"type": "string"},
                "objective_priority": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "hard_constraints",
                            "changed_sessions",
                            "affected_people",
                            "time_displacement",
                            "fairness",
                            "accessibility",
                            "sustainability",
                        ],
                    },
                },
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            ("disruption", "affected_session_ids", "frozen_session_ids", "schedule_revision"),
        ),
    ),
    ToolSpec(
        name="validate_repair_candidate",
        brief="Validate a candidate patch against current clashes, capacities, requirements, accessibility rules, and policies.",
        input_schema=_object(
            {
                "patch": PATCH,
                "schedule_revision": {"type": "string"},
                "constraint_ids": ID_LIST,
            },
            ("patch", "schedule_revision"),
        ),
    ),
    ToolSpec(
        name="calculate_repair_impact",
        brief="Measure changed sessions, affected people, displacement, fairness, accessibility, sustainability, and operational cost.",
        input_schema=_object(
            {
                "patch": PATCH,
                "baseline_revision": {"type": "string"},
                "include_person_ids": {"type": "boolean", "default": False},
            },
            ("patch", "baseline_revision"),
        ),
    ),
    ToolSpec(
        name="record_change_decision",
        brief="Record an authorized department or administrator decision on a proposed repair for audit and further planning.",
        input_schema=_object(
            {
                "candidate_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approved", "rejected", "revision_requested"]},
                "decided_by": {"type": "string"},
                "reason": {"type": "string"},
            },
            ("candidate_id", "decision", "decided_by"),
        ),
        write_operation=True,
    ),
    ToolSpec(
        name="apply_schedule_patch",
        brief="Atomically apply an approved, freshly validated patch to the authoritative timetable using optimistic revision checks.",
        input_schema=_object(
            {
                "patch": PATCH,
                "approval_id": {"type": "string"},
                "expected_schedule_revision": {"type": "string"},
                "validation_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
            },
            ("patch", "approval_id", "expected_schedule_revision", "validation_id", "dry_run"),
        ),
        write_operation=True,
    ),
    ToolSpec(
        name="publish_schedule_changes",
        brief="Publish an applied change set to calendars and stakeholder notification channels; never call before a successful apply.",
        input_schema=_object(
            {
                "change_set_id": {"type": "string"},
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["timetable", "email", "calendar", "portal"]},
                    "uniqueItems": True,
                },
                "audience_ids": ID_LIST,
                "message": {"type": "string"},
            },
            ("change_set_id", "channels", "message"),
        ),
        write_operation=True,
    ),
)


TOOL_SPEC_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}

