"""Pydantic models for the public MEGA-SHS API contract."""

from __future__ import annotations

from datetime import date as Date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mega_shs import SCHEMA_VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class HealingRunStatus(str, Enum):
    PROCESSING = "processing"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    STALE = "stale"


class CancellationType(str, Enum):
    EVENTS = "events"
    DAY = "day"


class ProposedActionType(str, Enum):
    MOVE_TIME = "move_time"
    MOVE_DATE = "move_date"


class EventType(str, Enum):
    LECTURE = "lecture"
    TUTORIAL = "tutorial"
    QUIZ = "quiz"
    EXAM = "exam"


class EventStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class ChangeSource(str, Enum):
    USER = "user"
    AGENT = "agent"


class CreateHealingRunRequest(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        json_schema_extra={
            "examples": [
                {
                    "cancellation_type": "events",
                    "event_ids": ["event_001", "event_002"],
                },
                {"cancellation_type": "day", "date": "2026-07-08"},
            ]
        },
    )
    cancellation_type: CancellationType
    event_ids: list[str] | None = None
    date: Date | None = None

    @field_validator("event_ids")
    @classmethod
    def normalize_event_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            event_id = raw.strip()
            if not event_id:
                raise ValueError("event_ids cannot contain blank values")
            if event_id not in seen:
                normalized.append(event_id)
                seen.add(event_id)
        return normalized

    @model_validator(mode="after")
    def validate_cancellation_shape(self) -> "CreateHealingRunRequest":
        if self.cancellation_type == CancellationType.EVENTS:
            if not self.event_ids:
                raise ValueError(
                    "event_ids must be present and non-empty for event cancellation"
                )
            if self.date is not None:
                raise ValueError("date conflicts with an event cancellation")
        else:
            if self.date is None:
                raise ValueError("date must be present for day cancellation")
            if self.event_ids is not None:
                raise ValueError("event_ids conflict with a day cancellation")
        return self


class RequestedCancellation(StrictModel):
    cancellation_type: CancellationType
    date: Date | None = None
    event_ids: list[str] = Field(default_factory=list)


class CreateHealingRunResponse(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1.0",
                    "run_id": "run_01K123ABC",
                    "status": "processing",
                    "created_at": "2026-07-28T09:42:00Z",
                }
            ]
        },
    )
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    status: Literal["processing"] = "processing"
    created_at: datetime


class ScheduleEventIdentity(StrictModel):
    name: str
    type: EventType
    room: str
    student_group: str


class SchedulePosition(StrictModel):
    date: Date
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    room: str = Field(min_length=1, max_length=200)

    @field_validator("room")
    @classmethod
    def normalize_room(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("room cannot be blank")
        return normalized


class ChangeDisplay(StrictModel):
    title: str
    detail: str
    status_label: str | None = None


class ProposedScheduleAction(StrictModel):
    action_id: str
    action_type: ProposedActionType
    event_id: str
    event: ScheduleEventIdentity
    previous: SchedulePosition
    proposed: SchedulePosition
    reason: str
    display: ChangeDisplay


class ApiError(StrictModel):
    code: str
    message: str
    details: Any | None = None


class ApiErrorResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    error: ApiError


class HealingRunResponse(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1.0",
                    "run_id": "run_01K123ABC",
                    "status": "approval_required",
                    "created_at": "2026-07-28T09:42:00Z",
                    "completed_at": "2026-07-28T09:42:18Z",
                    "schedule_version": "sha256:abcdef123456",
                    "summary": "One schedule activity must move.",
                    "requested_cancellation": {
                        "cancellation_type": "events",
                        "date": None,
                        "event_ids": ["event_001"],
                    },
                    "proposed_actions": [],
                    "errors": [],
                    "error": None,
                }
            ]
        },
    )
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    status: HealingRunStatus
    created_at: datetime
    completed_at: datetime | None = None
    schedule_version: str
    summary: str | None = None
    requested_cancellation: RequestedCancellation
    proposed_actions: list[ProposedScheduleAction] = Field(default_factory=list)
    errors: list[ApiError] = Field(default_factory=list)
    error: ApiError | None = None


class ApproveHealingRunResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    status: Literal["approved", "stale"]
    approved_at: datetime | None = None
    summary: str | None = None
    applied_action_count: int = 0
    schedule_version: str | None = None
    error: ApiError | None = None


class RejectHealingRunResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    status: Literal["rejected"] = "rejected"
    rejected_at: datetime


class ChangeHistoryEntry(StrictModel):
    action_id: str
    source: ChangeSource
    action_type: Literal["cancel", "move_time", "move_date"]
    display: ChangeDisplay


class ChangeHistoryGroup(StrictModel):
    run_id: str
    timestamp: datetime
    summary: str
    requested_cancellation: RequestedCancellation
    changes: list[ChangeHistoryEntry]


class ChangeHistoryResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    groups: list[ChangeHistoryGroup] = Field(default_factory=list)


class ScheduleEventResponse(StrictModel):
    id: str
    name: str
    room: str
    type: EventType
    student_group: str
    date: Date
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    status: EventStatus = EventStatus.ACTIVE


class ScheduleResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    schedule_version: str
    generated_at: datetime
    events: list[ScheduleEventResponse]


class ExportScheduleResponse(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    status: Literal["not_implemented"] = "not_implemented"
    message: str = "Schedule export is not implemented yet."
