"""Application service for asynchronous schedule-healing runs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from collections import defaultdict
from datetime import UTC, datetime

from mega_shs.agent_adapter import HealingAgent
from mega_shs.api_models import (
    ApiError,
    ApproveHealingRunResponse,
    ChangeHistoryEntry,
    ChangeHistoryGroup,
    ChangeHistoryResponse,
    ChangeSource,
    CreateHealingRunRequest,
    CreateHealingRunResponse,
    EventStatus,
    ExportScheduleResponse,
    HealingRunResponse,
    HealingRunStatus,
    RejectHealingRunResponse,
    RequestedCancellation,
    ScheduleEventResponse,
    ScheduleResponse,
)
from mega_shs.domain import StoredHealingRun, StoredPreview
from mega_shs.errors import (
    AgentExecutionError,
    AgentOutputError,
    ApiContractError,
    ToolExecutionError,
)
from mega_shs.exporter import (
    ExportNotImplementedError,
    ScheduleExporter,
)
from mega_shs.formatters import format_cancellation_display
from mega_shs.proposal_validation import (
    InvalidProposal,
    revalidate_actions,
    validate_agent_result,
)
from mega_shs.repository import HealingRunRepository
from mega_shs.schedule import (
    ExcelScheduleLoader,
    NormalizedSchedule,
    preview_schedule_hash,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    timestamp = int(utc_now().timestamp() * 1_000)
    return f"run_{timestamp:012x}{secrets.token_hex(6)}"


def _cancel_action_id(event_id: str) -> str:
    digest = hashlib.sha256(f"cancel|{event_id}".encode("utf-8")).hexdigest()[:16]
    return f"cancellation_{digest}"


class BackgroundTaskManager:
    """Keep strong references to internal asyncio tasks until they finish."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class HealingRunService:
    def __init__(
        self,
        *,
        repository: HealingRunRepository,
        schedule_loader: ExcelScheduleLoader,
        agent: HealingAgent,
        exporter: ScheduleExporter,
    ) -> None:
        self.repository = repository
        self.schedule_loader = schedule_loader
        self.agent = agent
        self.exporter = exporter
        self._workspace_locks: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    async def _source_schedule(self) -> NormalizedSchedule:
        return await asyncio.to_thread(self.schedule_loader.load)

    async def _current_events(
        self, workspace_id: str, source: NormalizedSchedule
    ) -> tuple[list[ScheduleEventResponse], str | None]:
        preview = await self.repository.get_preview(workspace_id)
        if preview:
            return [event.model_copy(deep=True) for event in preview.events], preview.version
        return [
            event.public.model_copy(deep=True) for event in source.events
        ], None

    async def create_run(
        self, workspace_id: str, request: CreateHealingRunRequest
    ) -> tuple[CreateHealingRunResponse, str]:
        source = await self._source_schedule()
        current_events, preview_version = await self._current_events(
            workspace_id, source
        )
        events_by_id = {event.id: event for event in current_events}

        if request.cancellation_type == "events":
            requested_ids = list(request.event_ids or [])
            missing = [event_id for event_id in requested_ids if event_id not in events_by_id]
            if missing:
                raise ApiContractError(
                    404,
                    "EVENT_NOT_FOUND",
                    "One or more referenced events do not exist in the current schedule.",
                    {"event_ids": missing},
                )
            selected = [events_by_id[event_id] for event_id in requested_ids]
            cancellation_date = None
        else:
            cancellation_date = request.date
            selected = [
                event for event in current_events if event.date == cancellation_date
            ]
            if not selected:
                raise ApiContractError(
                    404,
                    "EVENT_NOT_FOUND",
                    "No schedule events exist on the requested date.",
                )
            requested_ids = [event.id for event in selected]

        already_cancelled = [
            event.id for event in selected if event.status == EventStatus.CANCELLED
        ]
        if already_cancelled:
            raise ApiContractError(
                409,
                "INVALID_CANCELLATION",
                "One or more selected events are already cancelled.",
                {"event_ids": already_cancelled},
            )

        run_id = _run_id()
        created_at = utc_now()
        cancellation = RequestedCancellation(
            cancellation_type=request.cancellation_type,
            date=cancellation_date,
            event_ids=requested_ids,
        )
        run_response = HealingRunResponse(
            run_id=run_id,
            status=HealingRunStatus.PROCESSING,
            created_at=created_at,
            schedule_version=source.source_version,
            requested_cancellation=cancellation,
        )
        await self.repository.save_run(
            StoredHealingRun(
                workspace_id=workspace_id,
                response=run_response,
                source_schedule_version=source.source_version,
                preview_version_at_creation=preview_version,
            )
        )
        return (
            CreateHealingRunResponse(
                run_id=run_id,
                status="processing",
                created_at=created_at,
            ),
            run_id,
        )

    async def process_run(self, workspace_id: str, run_id: str) -> None:
        run = await self.repository.get_run(workspace_id, run_id)
        if run is None:
            return
        try:
            source = await self._source_schedule()
            if source.source_version != run.source_schedule_version:
                await self._mark_stale(run)
                return
            current_events, _ = await self._current_events(workspace_id, source)
            events_by_id = {event.id: event for event in current_events}
            cancelled_events = [
                events_by_id[event_id]
                for event_id in run.response.requested_cancellation.event_ids
                if event_id in events_by_id
            ]
            if len(cancelled_events) != len(
                run.response.requested_cancellation.event_ids
            ):
                await self._mark_stale(run)
                return

            agent_result = await self.agent.propose(
                run_id=run_id,
                cancellation=run.response.requested_cancellation,
                cancelled_events=cancelled_events,
                source_schedule=source,
            )
            try:
                actions = validate_agent_result(
                    agent_result,
                    source,
                    current_events,
                    run.response.requested_cancellation,
                )
            except InvalidProposal as first_error:
                logger.warning(
                    "Healing proposal failed validation for run %s; retrying "
                    "once with corrective feedback: %s",
                    run_id,
                    first_error,
                )
                replacement_result = await self.agent.propose(
                    run_id=run_id,
                    cancellation=run.response.requested_cancellation,
                    cancelled_events=cancelled_events,
                    source_schedule=source,
                    retry_feedback=str(first_error),
                    rejected_result=agent_result,
                )
                if replacement_result == agent_result:
                    raise InvalidProposal(
                        "The retry repeated the rejected proposal."
                    ) from first_error
                agent_result = replacement_result
                actions = validate_agent_result(
                    agent_result,
                    source,
                    current_events,
                    run.response.requested_cancellation,
                )
            run.response.status = HealingRunStatus.APPROVAL_REQUIRED
            run.response.completed_at = utc_now()
            run.response.summary = agent_result.summary.strip()
            run.response.proposed_actions = actions
            run.response.error = None
            run.response.errors = []
            await self.repository.save_run(run)
        except AgentOutputError:
            await self._mark_failed(
                run,
                "INVALID_AGENT_OUTPUT",
                "The scheduling agent returned invalid structured output.",
            )
        except InvalidProposal:
            await self._mark_failed(
                run,
                "INVALID_AGENT_OUTPUT",
                "The scheduling agent returned a proposal that failed validation.",
            )
        except ToolExecutionError:
            await self._mark_failed(
                run,
                "TOOL_EXECUTION_FAILED",
                "A scheduling tool could not complete the healing run.",
            )
        except AgentExecutionError as exc:
            logger.exception(
                "Healing agent execution failed for run %s in workspace %s.",
                run_id,
                workspace_id,
            )
            await self._mark_failed(
                run,
                exc.code,
                exc.public_message,
            )
        except Exception:
            logger.exception(
                "Unexpected healing-run failure for run %s in workspace %s.",
                run_id,
                workspace_id,
            )
            await self._mark_failed(
                run,
                "AGENT_EXECUTION_FAILED",
                "The scheduling agent could not complete the healing run.",
            )

    async def _mark_failed(
        self, run: StoredHealingRun, code: str, message: str
    ) -> None:
        error = ApiError(code=code, message=message, details=None)
        run.response.status = HealingRunStatus.FAILED
        run.response.completed_at = utc_now()
        run.response.error = error
        run.response.errors = [error]
        await self.repository.save_run(run)

    async def _mark_stale(self, run: StoredHealingRun) -> None:
        error = ApiError(
            code="SCHEDULE_VERSION_CONFLICT",
            message=(
                "The source schedule changed after this healing run was created. "
                "Create a new healing run before approving changes."
            ),
            details=None,
        )
        run.response.status = HealingRunStatus.STALE
        run.response.completed_at = utc_now()
        run.response.error = error
        run.response.errors = [error]
        await self.repository.save_run(run)

    async def get_run(
        self, workspace_id: str, run_id: str
    ) -> HealingRunResponse:
        run = await self.repository.get_run(workspace_id, run_id)
        if run is None:
            raise ApiContractError(
                404, "RUN_NOT_FOUND", "The healing run does not exist."
            )
        return run.response

    async def approve(
        self, workspace_id: str, run_id: str
    ) -> ApproveHealingRunResponse:
        async with self._workspace_locks[workspace_id]:
            run = await self.repository.get_run(workspace_id, run_id)
            if run is None:
                raise ApiContractError(
                    404, "RUN_NOT_FOUND", "The healing run does not exist."
                )
            if run.response.status in {
                HealingRunStatus.APPROVED,
                HealingRunStatus.REJECTED,
                HealingRunStatus.STALE,
            }:
                raise ApiContractError(
                    409,
                    "RUN_ALREADY_RESOLVED",
                    "The healing run has already been resolved.",
                )
            if run.response.status != HealingRunStatus.APPROVAL_REQUIRED:
                raise ApiContractError(
                    409,
                    "RUN_NOT_APPROVABLE",
                    "The healing run is not ready for approval.",
                )

            source = await self._source_schedule()
            if source.source_version != run.source_schedule_version:
                return await self._stale_approval(run)
            current_events, _ = await self._current_events(workspace_id, source)
            by_id = {event.id: event for event in current_events}
            cancellation_ids = run.response.requested_cancellation.event_ids
            if any(
                event_id not in by_id
                or by_id[event_id].status == EventStatus.CANCELLED
                for event_id in cancellation_ids
            ):
                return await self._stale_approval(run)
            try:
                revalidate_actions(
                    run.response.proposed_actions,
                    source,
                    current_events,
                    run.response.requested_cancellation,
                )
            except InvalidProposal:
                return await self._stale_approval(run)

            preview_by_id = {
                event.id: event.model_copy(deep=True) for event in current_events
            }
            cancelled_events: list[ScheduleEventResponse] = []
            try:
                for event_id in cancellation_ids:
                    cancelled = preview_by_id[event_id].model_copy(
                        update={"status": EventStatus.CANCELLED}
                    )
                    preview_by_id[event_id] = cancelled
                    cancelled_events.append(cancelled)
                for action in run.response.proposed_actions:
                    current = preview_by_id[action.event_id]
                    preview_by_id[action.event_id] = current.model_copy(
                        update={
                            "date": action.proposed.date,
                            "start_time": action.proposed.start_time,
                            "end_time": action.proposed.end_time,
                            "room": action.proposed.room,
                        }
                    )
            except Exception as exc:
                raise ApiContractError(
                    500,
                    "PREVIEW_BUILD_FAILED",
                    "The approved schedule preview could not be created.",
                ) from exc

            preview_events = sorted(
                preview_by_id.values(),
                key=lambda event: (
                    event.date,
                    event.start_time,
                    event.end_time,
                    event.id,
                ),
            )
            preview_version = preview_schedule_hash(preview_events)
            approved_at = utc_now()
            await self.repository.save_preview(
                StoredPreview(
                    workspace_id=workspace_id,
                    version=preview_version,
                    updated_at=approved_at,
                    events=preview_events,
                )
            )

            action_count = len(run.response.proposed_actions)
            run.response.status = HealingRunStatus.APPROVED
            run.response.completed_at = approved_at
            await self.repository.save_run(run)
            await self.repository.add_history(
                workspace_id,
                self._history_group(run, cancelled_events, approved_at),
            )
            cancellation_count = len(cancellation_ids)
            move_word = "move" if action_count == 1 else "moves"
            summary = (
                f"The cancellation of {cancellation_count} schedule "
                f"{'activity' if cancellation_count == 1 else 'activities'} "
                f"and {action_count} schedule {move_word} were applied to the "
                "schedule preview."
            )
            return ApproveHealingRunResponse(
                run_id=run_id,
                status="approved",
                approved_at=approved_at,
                summary=summary,
                applied_action_count=action_count,
                schedule_version=preview_version,
            )

    async def _stale_approval(
        self, run: StoredHealingRun
    ) -> ApproveHealingRunResponse:
        await self._mark_stale(run)
        return ApproveHealingRunResponse(
            run_id=run.response.run_id,
            status="stale",
            applied_action_count=0,
            error=run.response.error,
        )

    def _history_group(
        self,
        run: StoredHealingRun,
        cancelled_events: list[ScheduleEventResponse],
        approved_at: datetime,
    ) -> ChangeHistoryGroup:
        changes = [
            ChangeHistoryEntry(
                action_id=_cancel_action_id(event.id),
                source=ChangeSource.USER,
                action_type="cancel",
                display=format_cancellation_display(event),
            )
            for event in cancelled_events
        ]
        changes.extend(
            ChangeHistoryEntry(
                action_id=action.action_id,
                source=ChangeSource.AGENT,
                action_type=action.action_type,
                display=action.display,
            )
            for action in run.response.proposed_actions
        )
        move_count = len(run.response.proposed_actions)
        return ChangeHistoryGroup(
            run_id=run.response.run_id,
            timestamp=approved_at,
            summary=(
                f"The cancellation and {move_count} schedule "
                f"{'move' if move_count == 1 else 'moves'} were approved."
            ),
            requested_cancellation=run.response.requested_cancellation,
            changes=changes,
        )

    async def reject(
        self, workspace_id: str, run_id: str
    ) -> RejectHealingRunResponse:
        async with self._workspace_locks[workspace_id]:
            run = await self.repository.get_run(workspace_id, run_id)
            if run is None:
                raise ApiContractError(
                    404, "RUN_NOT_FOUND", "The healing run does not exist."
                )
            if run.response.status in {
                HealingRunStatus.APPROVED,
                HealingRunStatus.REJECTED,
                HealingRunStatus.STALE,
            }:
                raise ApiContractError(
                    409,
                    "RUN_ALREADY_RESOLVED",
                    "The healing run has already been resolved.",
                )
            if run.response.status != HealingRunStatus.APPROVAL_REQUIRED:
                raise ApiContractError(
                    409,
                    "RUN_NOT_APPROVABLE",
                    "The healing run is not ready for rejection.",
                )
            rejected_at = utc_now()
            run.response.status = HealingRunStatus.REJECTED
            run.response.completed_at = rejected_at
            await self.repository.save_run(run)
            return RejectHealingRunResponse(
                run_id=run_id,
                status="rejected",
                rejected_at=rejected_at,
            )

    async def schedule(self, workspace_id: str) -> ScheduleResponse:
        source = await self._source_schedule()
        preview = await self.repository.get_preview(workspace_id)
        if preview:
            version = preview.version
            events = preview.events
        else:
            version = source.source_version
            events = [item.public for item in source.events]
        return ScheduleResponse(
            schedule_version=version,
            generated_at=utc_now(),
            events=events,
        )

    async def history(self, workspace_id: str) -> ChangeHistoryResponse:
        return ChangeHistoryResponse(
            groups=await self.repository.get_history(workspace_id)
        )

    async def export(self, workspace_id: str) -> ExportScheduleResponse:
        schedule = await self.schedule(workspace_id)
        try:
            await self.exporter.export_schedule(schedule.events)
        except ExportNotImplementedError:
            return ExportScheduleResponse()
        raise ApiContractError(
            500,
            "EXPORT_NOT_IMPLEMENTED",
            "Schedule export is not implemented yet.",
        )


def max_stored_runs() -> int:
    try:
        return max(1, int(os.getenv("MEGA_SHS_MAX_STORED_RUNS", "200")))
    except ValueError:
        return 200
