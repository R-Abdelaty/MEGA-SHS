"""Future schedule export boundary.

The current implementation is deliberately unavailable and never opens a
source workbook for writing. A future exporter should consume the approved
normalized preview and write a separate output file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mega_shs.api_models import ScheduleEventResponse


@dataclass(frozen=True)
class ExportedSchedule:
    file_name: str
    content_type: str
    size_bytes: int


class ScheduleExporter(Protocol):
    async def export_schedule(
        self,
        schedule: list[ScheduleEventResponse],
        output_format: str = "xlsx",
    ) -> ExportedSchedule: ...


class ExportNotImplementedError(NotImplementedError):
    pass


class PlaceholderScheduleExporter:
    async def export_schedule(
        self,
        schedule: list[ScheduleEventResponse],
        output_format: str = "xlsx",
    ) -> ExportedSchedule:
        # TODO: Implement a separate-file XLSX writer here. Never save over any
        # path under the authoritative fake-data schedule source set.
        raise ExportNotImplementedError("Schedule export is not implemented yet.")

