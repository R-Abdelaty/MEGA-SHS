"""Structured output owned by the LangChain scheduling agent."""

from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentEventReference(AgentStrictModel):
    event_id: str | None = None
    source_event_id: str | None = None

    @model_validator(mode="after")
    def require_reference(self) -> "AgentEventReference":
        if not self.event_id and not self.source_event_id:
            raise ValueError("event_id or source_event_id is required")
        return self


class AgentSchedulePosition(AgentStrictModel):
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


class AgentProposedMove(AgentStrictModel):
    action_type: Literal["move_time", "move_date"]
    event_reference: AgentEventReference
    previous: AgentSchedulePosition
    proposed: AgentSchedulePosition
    reason: str = Field(min_length=1, max_length=800)


class AgentHealingResult(AgentStrictModel):
    summary: str = Field(min_length=1, max_length=1_000)
    proposed_moves: list[AgentProposedMove] = Field(default_factory=list)
