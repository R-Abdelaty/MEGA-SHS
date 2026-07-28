"""Serializable internal domain records."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from mega_shs.api_models import (
    ChangeHistoryGroup,
    HealingRunResponse,
    ScheduleEventResponse,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredHealingRun(DomainModel):
    workspace_id: str
    response: HealingRunResponse
    source_schedule_version: str
    preview_version_at_creation: str | None = None


class StoredPreview(DomainModel):
    workspace_id: str
    version: str
    updated_at: datetime
    events: list[ScheduleEventResponse]


class WorkspaceSnapshot(DomainModel):
    preview: StoredPreview | None = None
    history: list[ChangeHistoryGroup] = Field(default_factory=list)
