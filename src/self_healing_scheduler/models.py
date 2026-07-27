"""Small shared domain model; expand it as connector schemas become known."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionType(StrEnum):
    LECTURE = "lecture"
    TUTORIAL = "tutorial"
    LABORATORY = "laboratory"
    WORKSHOP = "workshop"
    EXAMINATION = "examination"
    PRESENTATION = "presentation"
    FACULTY_MEETING = "faculty_meeting"
    VISITING_PROFESSOR = "visiting_professor"
    STUDENT_SUPPORT = "student_support"


class DisruptionType(StrEnum):
    LECTURER_UNAVAILABLE = "lecturer_unavailable"
    ROOM_CLOSED = "room_closed"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    LIMITED_VISITOR_AVAILABILITY = "limited_visitor_availability"
    EXAMINATION_ADDED = "examination_added"
    CAPACITY_CORRECTED = "capacity_corrected"
    UNIVERSITY_EVENT = "university_event"
    CHANGE_REJECTED = "change_rejected"
    OTHER = "other"


class ConstraintSeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    session_type: SessionType
    title: str
    time: TimeRange
    room_id: str | None = None
    lecturer_ids: tuple[str, ...] = ()
    cohort_ids: tuple[str, ...] = ()
    equipment_ids: tuple[str, ...] = ()
    expected_attendance: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Disruption:
    disruption_id: str
    disruption_type: DisruptionType
    description: str
    affected_ids: tuple[str, ...]
    effective_time: TimeRange | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionChange:
    session_id: str
    before: Session | None
    after: Session | None
    reason: str


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    candidate_id: str
    changes: tuple[SessionChange, ...]
    relaxed_soft_constraint_ids: tuple[str, ...] = ()
    score: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    candidate_id: str
    valid: bool
    hard_violations: tuple[dict[str, Any], ...] = ()
    soft_violations: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImpactReport:
    candidate_id: str
    changed_session_count: int
    affected_student_count: int
    affected_lecturer_count: int
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

