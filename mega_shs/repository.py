"""Async-safe in-memory persistence for healing runs and previews.

All contents are process-local and disappear whenever FastAPI restarts.  The
protocol is intentionally small so a PostgreSQL implementation can replace it
without changing routes or the healing service.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Protocol

from mega_shs.api_models import ChangeHistoryGroup, HealingRunStatus
from mega_shs.domain import StoredHealingRun, StoredPreview


class HealingRunRepository(Protocol):
    async def save_run(self, run: StoredHealingRun) -> None: ...

    async def get_run(
        self, workspace_id: str, run_id: str
    ) -> StoredHealingRun | None: ...

    async def save_preview(self, preview: StoredPreview) -> None: ...

    async def get_preview(self, workspace_id: str) -> StoredPreview | None: ...

    async def add_history(
        self, workspace_id: str, group: ChangeHistoryGroup
    ) -> None: ...

    async def get_history(self, workspace_id: str) -> list[ChangeHistoryGroup]: ...


class InMemoryHealingRunRepository:
    """Bounded, async-safe repository scoped by explicit workspace IDs."""

    def __init__(self, max_runs: int = 200) -> None:
        if max_runs < 1:
            raise ValueError("max_runs must be positive")
        self.max_runs = max_runs
        self._lock = asyncio.Lock()
        self._runs: dict[str, OrderedDict[str, StoredHealingRun]] = {}
        self._previews: dict[str, StoredPreview] = {}
        self._history: dict[str, list[ChangeHistoryGroup]] = {}

    async def save_run(self, run: StoredHealingRun) -> None:
        async with self._lock:
            workspace_runs = self._runs.setdefault(run.workspace_id, OrderedDict())
            workspace_runs[run.response.run_id] = run.model_copy(deep=True)
            workspace_runs.move_to_end(run.response.run_id)
            self._cleanup(workspace_runs)

    async def get_run(
        self, workspace_id: str, run_id: str
    ) -> StoredHealingRun | None:
        async with self._lock:
            run = self._runs.get(workspace_id, {}).get(run_id)
            return run.model_copy(deep=True) if run else None

    async def save_preview(self, preview: StoredPreview) -> None:
        async with self._lock:
            self._previews[preview.workspace_id] = preview.model_copy(deep=True)

    async def get_preview(self, workspace_id: str) -> StoredPreview | None:
        async with self._lock:
            preview = self._previews.get(workspace_id)
            return preview.model_copy(deep=True) if preview else None

    async def add_history(
        self, workspace_id: str, group: ChangeHistoryGroup
    ) -> None:
        async with self._lock:
            groups = self._history.setdefault(workspace_id, [])
            groups.insert(0, group.model_copy(deep=True))

    async def get_history(self, workspace_id: str) -> list[ChangeHistoryGroup]:
        async with self._lock:
            return [
                group.model_copy(deep=True)
                for group in self._history.get(workspace_id, [])
            ]

    def _cleanup(
        self, workspace_runs: OrderedDict[str, StoredHealingRun]
    ) -> None:
        """Remove the oldest resolved runs first; never evict active work."""
        while len(workspace_runs) > self.max_runs:
            removable = next(
                (
                    run_id
                    for run_id, run in workspace_runs.items()
                    if run.response.status != HealingRunStatus.PROCESSING
                ),
                None,
            )
            if removable is None:
                break
            del workspace_runs[removable]

