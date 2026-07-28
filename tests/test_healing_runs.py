"""Focused tests for the validated healing-run application layer."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

import httpx
from openai import AuthenticationError as OpenAIAuthenticationError
from pydantic import ValidationError
from fastapi.testclient import TestClient

import api
from mega_shs.agent_adapter import (
    LangChainHealingAgent,
    authoritative_source_manifest,
)
from mega_shs.agent_models import AgentHealingResult
from mega_shs.api_models import (
    CreateHealingRunRequest,
    EventStatus,
    EventType,
    ScheduleEventResponse,
    SchedulePosition,
)
from mega_shs.errors import AgentExecutionError, AgentOutputError, ApiContractError
from mega_shs.exporter import PlaceholderScheduleExporter
from mega_shs.formatters import format_change_detail, format_cancellation_display
from mega_shs.repository import InMemoryHealingRunRepository
from mega_shs.schedule import (
    NormalizedEvent,
    NormalizedSchedule,
    schedule_source_hash,
    stable_event_id,
)
from mega_shs.service import HealingRunService
from mega_shs.routes import get_service, get_task_manager


def schedule_event(
    event_id: str,
    *,
    event_date: date,
    start: str,
    end: str,
    room: str = "A101",
    group: str = "G1",
    status: EventStatus = EventStatus.ACTIVE,
) -> ScheduleEventResponse:
    return ScheduleEventResponse(
        id=event_id,
        name=f"Course {event_id}",
        room=room,
        type=EventType.LECTURE,
        student_group=group,
        date=event_date,
        start_time=start,
        end_time=end,
        status=status,
    )


def normalized(event: ScheduleEventResponse, row: int) -> NormalizedEvent:
    return NormalizedEvent(
        public=event,
        source_event_id=f"SRC-{event.id}",
        source_file="05_General_Schedule.xlsx",
        source_sheet="Semester Timetable",
        source_row=row,
    )


class FakeLoader:
    def __init__(self) -> None:
        self.version = "sha256:test"
        self.events = [
            normalized(
                schedule_event(
                    "event_cancel",
                    event_date=date(2026, 7, 8),
                    start="10:00",
                    end="11:00",
                ),
                5,
            ),
            normalized(
                schedule_event(
                    "event_move",
                    event_date=date(2026, 7, 9),
                    start="10:00",
                    end="11:00",
                ),
                6,
            ),
            normalized(
                schedule_event(
                    "event_other",
                    event_date=date(2026, 7, 9),
                    start="14:00",
                    end="15:00",
                    room="B202",
                    group="G2",
                ),
                7,
            ),
        ]

    def load(self) -> NormalizedSchedule:
        return NormalizedSchedule(
            source_version=self.version,
            events=[event.model_copy(deep=True) for event in self.events],
            rooms=["A101", "B202", "C303"],
        )


class FakeAgent:
    def __init__(
        self,
        result: AgentHealingResult | None = None,
        *,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.result = result or AgentHealingResult(
            summary="One related activity must move.",
            proposed_moves=[
                {
                    "action_type": "move_time",
                    "event_reference": {"event_id": "event_move"},
                    "previous": {
                        "date": "2026-07-09",
                        "start_time": "10:00",
                        "end_time": "11:00",
                        "room": "A101",
                    },
                    "proposed": {
                        "date": "2026-07-09",
                        "start_time": "12:00",
                        "end_time": "13:00",
                        "room": "A101",
                    },
                    "reason": "The room and student group are available.",
                }
            ],
        )
        self.delay = delay
        self.error = error

    async def propose(self, **_) -> AgentHealingResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class SequenceAgent:
    def __init__(self, results: list[AgentHealingResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def propose(self, **kwargs) -> AgentHealingResult:
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("The agent was called more times than expected.")
        return self.results.pop(0)


class RaisingGraph:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def invoke(self, *_args, **_kwargs):
        raise self.error


def service(
    loader: FakeLoader | None = None,
    agent: FakeAgent | None = None,
) -> HealingRunService:
    return HealingRunService(
        repository=InMemoryHealingRunRepository(max_runs=20),
        schedule_loader=loader or FakeLoader(),
        agent=agent or FakeAgent(),
        exporter=PlaceholderScheduleExporter(),
    )


class CancellationModelTests(unittest.TestCase):
    def test_event_cancellation_requires_nonempty_ids(self) -> None:
        with self.assertRaises(ValidationError):
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=[],
            )

    def test_day_cancellation_rejects_conflicting_event_ids(self) -> None:
        with self.assertRaises(ValidationError):
            CreateHealingRunRequest(
                cancellation_type="day",
                date="2026-07-08",
                event_ids=["event_cancel"],
            )

    def test_duplicate_event_ids_are_normalized(self) -> None:
        request = CreateHealingRunRequest(
            cancellation_type="events",
            event_ids=["event_cancel", "event_cancel"],
        )
        self.assertEqual(request.event_ids, ["event_cancel"])

    def test_agent_schema_rejects_unsupported_action_type(self) -> None:
        for action_type in ("cancel", "change_room"):
            with self.subTest(action_type=action_type), self.assertRaises(
                ValidationError
            ):
                AgentHealingResult(
                    summary="Invalid",
                    proposed_moves=[
                        {
                            "action_type": action_type,
                            "event_reference": {"event_id": "event_cancel"},
                            "previous": {
                                "date": "2026-07-08",
                                "start_time": "10:00",
                                "end_time": "11:00",
                                "room": "A101",
                            },
                            "proposed": {
                                "date": "2026-07-08",
                                "start_time": "12:00",
                                "end_time": "13:00",
                                "room": "B202",
                            },
                            "reason": "Not allowed",
                        }
                    ],
                )


class IdentityAndFormattingTests(unittest.TestCase):
    def test_event_id_is_deterministic(self) -> None:
        arguments = {
            "source_file": "05_General_Schedule.xlsx",
            "sheet_name": "Semester Timetable",
            "row_number": 5,
            "source_event_id": "S1",
            "name": "Power Systems",
            "event_type": "lecture",
            "student_group": "G1",
            "event_date": date(2026, 7, 8),
            "start_time": "10:00",
            "end_time": "11:00",
            "room": "A101",
        }
        self.assertEqual(stable_event_id(**arguments), stable_event_id(**arguments))

    def test_schedule_hash_is_deterministic_and_order_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "01_A.xlsx"
            second = Path(directory) / "02_B.xlsx"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.assertEqual(
                schedule_source_hash([first, second]),
                schedule_source_hash([second, first]),
            )

    def test_display_formatters_are_deterministic(self) -> None:
        event = schedule_event(
            "event_cancel",
            event_date=date(2026, 7, 8),
            start="10:00",
            end="11:00",
        )
        display = format_cancellation_display(event)
        self.assertEqual(display.title, "Course event_cancel")
        self.assertEqual(display.detail, "CANCELLED · G1 · A101")
        self.assertEqual(
            format_change_detail(
                "move_time",
                SchedulePosition(
                    date=date(2026, 7, 8),
                    start_time="10:00",
                    end_time="11:00",
                    room="A101",
                ),
                SchedulePosition(
                    date=date(2026, 7, 8),
                    start_time="12:00",
                    end_time="13:00",
                    room="A101",
                ),
            ),
            "Wednesday 10:00 → Wednesday 12:00",
        )
        self.assertEqual(
            format_change_detail(
                "move_date",
                SchedulePosition(
                    date=date(2026, 7, 8),
                    start_time="10:00",
                    end_time="11:00",
                    room="A101",
                ),
                SchedulePosition(
                    date=date(2026, 7, 9),
                    start_time="12:00",
                    end_time="13:00",
                    room="B202",
                ),
            ),
            "Wednesday, Jul 8 10:00 · A101 → Thursday, Jul 9 12:00 · B202",
            )


class AgentAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_authoritative_source_manifest_exposes_all_fake_data_inputs(
        self,
    ) -> None:
        manifest = authoritative_source_manifest()
        self.assertEqual(
            {item["file_name"] for item in manifest},
            {
                "01_Room_Schedule.xlsx",
                "02_Lab_Equipment.xlsx",
                "03_Student_Enrollment.xlsx",
                "04_Course_Catalog.xlsx",
                "05_General_Schedule.xlsx",
                "06_Exam_Schedule.xlsx",
                "07_Doctor_Schedule_Calendar.xlsx",
            },
        )
        self.assertTrue(all(item["purpose"] for item in manifest))

    async def test_litellm_authentication_error_is_actionable(self) -> None:
        source = FakeLoader().load()
        event = source.events[0].public
        request = httpx.Request(
            "POST",
            "https://litellm.i-hq.tech/v1/chat/completions",
        )
        response = httpx.Response(401, request=request)
        agent = LangChainHealingAgent()
        agent._agent = RaisingGraph(
            OpenAIAuthenticationError(
                "invalid LiteLLM key",
                response=response,
                body={"error": {"type": "authentication_error"}},
            )
        )

        with self.assertRaises(AgentExecutionError) as context:
            await agent.propose(
                run_id="run_test",
                cancellation=CreateHealingRunRequest(
                    cancellation_type="events",
                    event_ids=[event.id],
                ),
                cancelled_events=[event],
                source_schedule=source,
            )

        self.assertEqual(
            context.exception.code,
            "LITELLM_AUTHENTICATION_FAILED",
        )
        self.assertIn(
            "LITELLM_API_KEY",
            context.exception.public_message,
        )


class HealingRunServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.loader = FakeLoader()
        self.service = service(loader=self.loader)
        self.workspace = "workspace-test"

    async def create(self) -> str:
        _, run_id = await self.service.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        return run_id

    async def ready(self) -> str:
        run_id = await self.create()
        await self.service.process_run(self.workspace, run_id)
        return run_id

    async def test_create_is_processing_then_polling_reaches_approval(self) -> None:
        run_id = await self.create()
        initial = await self.service.get_run(self.workspace, run_id)
        self.assertEqual(initial.status, "processing")
        await self.service.process_run(self.workspace, run_id)
        completed = await self.service.get_run(self.workspace, run_id)
        self.assertEqual(completed.status, "approval_required")
        self.assertEqual(len(completed.proposed_actions), 1)

    async def test_invalid_structured_output_marks_failed(self) -> None:
        tested = service(
            loader=self.loader,
            agent=FakeAgent(error=AgentOutputError("invalid")),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        run = await tested.get_run(self.workspace, run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "INVALID_AGENT_OUTPUT")

    async def test_invalid_proposal_retries_once_with_corrective_feedback(
        self,
    ) -> None:
        invalid_move = FakeAgent().result.proposed_moves[0].model_dump()
        invalid_move["event_reference"] = {"event_id": "event_missing"}
        invalid_result = AgentHealingResult(
            summary="Move an unresolved activity.",
            proposed_moves=[invalid_move],
        )
        replacement_result = FakeAgent().result
        agent = SequenceAgent([invalid_result, replacement_result])
        tested = service(loader=self.loader, agent=agent)

        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)

        run = await tested.get_run(self.workspace, run_id)
        self.assertEqual(run.status, "approval_required")
        self.assertEqual(len(run.proposed_actions), 1)
        self.assertEqual(len(agent.calls), 2)
        self.assertIsNone(agent.calls[0].get("retry_feedback"))
        self.assertIn(
            "could not be resolved",
            agent.calls[1]["retry_feedback"],
        )
        self.assertEqual(
            agent.calls[1]["rejected_result"],
            invalid_result,
        )

    async def test_repeated_invalid_retry_fails_without_a_third_attempt(
        self,
    ) -> None:
        invalid_move = FakeAgent().result.proposed_moves[0].model_dump()
        invalid_move["event_reference"] = {"event_id": "event_missing"}
        invalid_result = AgentHealingResult(
            summary="Move an unresolved activity.",
            proposed_moves=[invalid_move],
        )
        agent = SequenceAgent([invalid_result, invalid_result])
        tested = service(loader=self.loader, agent=agent)

        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)

        run = await tested.get_run(self.workspace, run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "INVALID_AGENT_OUTPUT")
        self.assertEqual(len(agent.calls), 2)

    async def test_agent_execution_error_preserves_safe_public_details(self) -> None:
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                error=AgentExecutionError(
                    "Provider diagnostic for server logs.",
                    code="LITELLM_AUTHENTICATION_FAILED",
                    public_message=(
                        "The iHQ LiteLLM gateway rejected LITELLM_API_KEY. Check "
                        "the key in the backend .env file and restart the API."
                    ),
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        with self.assertLogs("mega_shs.service", level="ERROR"):
            await tested.process_run(self.workspace, run_id)
        run = await tested.get_run(self.workspace, run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error.code, "LITELLM_AUTHENTICATION_FAILED")
        self.assertIn("LITELLM_API_KEY", run.error.message)
        self.assertNotIn("Provider diagnostic", run.error.message)

    async def test_duplicate_actions_fail_validation(self) -> None:
        base = FakeAgent().result.proposed_moves[0].model_dump()
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                AgentHealingResult(
                    summary="Duplicate",
                    proposed_moves=[base, base],
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        self.assertEqual(
            (await tested.get_run(self.workspace, run_id)).status,
            "failed",
        )

    async def test_contradictory_actions_fail_validation(self) -> None:
        first = FakeAgent().result.proposed_moves[0].model_dump()
        second = FakeAgent().result.proposed_moves[0].model_dump()
        second["proposed"] = {
            "date": date(2026, 7, 9),
            "start_time": "16:00",
            "end_time": "17:00",
            "room": "A101",
        }
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                AgentHealingResult(
                    summary="Contradictory",
                    proposed_moves=[first, second],
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        self.assertEqual(
            (await tested.get_run(self.workspace, run_id)).status,
            "failed",
        )

    async def test_approval_builds_preview_and_history(self) -> None:
        run_id = await self.ready()
        result = await self.service.approve(self.workspace, run_id)
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.applied_action_count, 1)

        preview = await self.service.schedule(self.workspace)
        by_id = {event.id: event for event in preview.events}
        self.assertEqual(by_id["event_cancel"].status, "cancelled")
        self.assertEqual(by_id["event_move"].start_time, "12:00")

        history = await self.service.history(self.workspace)
        self.assertEqual(len(history.groups), 1)
        self.assertEqual(
            [change.source for change in history.groups[0].changes],
            ["user", "agent"],
        )

    async def test_time_move_can_include_valid_room_change(self) -> None:
        changed_room = FakeAgent().result.proposed_moves[0].model_dump()
        changed_room["proposed"]["room"] = "B202"
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                AgentHealingResult(
                    summary="Move the activity into an available room.",
                    proposed_moves=[changed_room],
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        run = await tested.get_run(self.workspace, run_id)
        self.assertEqual(run.status, "approval_required")
        action = run.proposed_actions[0]
        self.assertEqual(action.previous.room, "A101")
        self.assertEqual(action.proposed.room, "B202")
        self.assertIn("A101", action.display.detail)
        self.assertIn("B202", action.display.detail)

        await tested.approve(self.workspace, run_id)
        preview = await tested.schedule(self.workspace)
        moved = next(event for event in preview.events if event.id == "event_move")
        self.assertEqual(moved.room, "B202")

    async def test_room_change_cannot_be_a_standalone_move(self) -> None:
        room_only = FakeAgent().result.proposed_moves[0].model_dump()
        room_only["proposed"] = {
            **room_only["previous"],
            "room": "B202",
        }
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                AgentHealingResult(
                    summary="Invalid room-only action.",
                    proposed_moves=[room_only],
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        self.assertEqual(
            (await tested.get_run(self.workspace, run_id)).status,
            "failed",
        )

    async def test_unknown_proposed_room_fails_validation(self) -> None:
        unknown_room = FakeAgent().result.proposed_moves[0].model_dump()
        unknown_room["proposed"]["room"] = "Imaginary Room"
        tested = service(
            loader=self.loader,
            agent=FakeAgent(
                AgentHealingResult(
                    summary="Invalid room.",
                    proposed_moves=[unknown_room],
                )
            ),
        )
        _, run_id = await tested.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="events",
                event_ids=["event_cancel"],
            ),
        )
        await tested.process_run(self.workspace, run_id)
        self.assertEqual(
            (await tested.get_run(self.workspace, run_id)).status,
            "failed",
        )

    async def test_rejection_leaves_schedule_unchanged(self) -> None:
        run_id = await self.ready()
        result = await self.service.reject(self.workspace, run_id)
        self.assertEqual(result.status, "rejected")
        preview = await self.service.schedule(self.workspace)
        by_id = {event.id: event for event in preview.events}
        self.assertEqual(by_id["event_cancel"].status, "active")
        self.assertEqual((await self.service.history(self.workspace)).groups, [])

    async def test_stale_approval_is_rejected(self) -> None:
        run_id = await self.ready()
        self.loader.version = "sha256:changed"
        result = await self.service.approve(self.workspace, run_id)
        self.assertEqual(result.status, "stale")
        self.assertEqual(result.error.code, "SCHEDULE_VERSION_CONFLICT")

    async def test_already_resolved_run_is_rejected(self) -> None:
        run_id = await self.ready()
        await self.service.reject(self.workspace, run_id)
        with self.assertRaises(ApiContractError) as context:
            await self.service.reject(self.workspace, run_id)
        self.assertEqual(context.exception.error.code, "RUN_ALREADY_RESOLVED")

    async def test_missing_run_has_stable_error(self) -> None:
        with self.assertRaises(ApiContractError) as context:
            await self.service.get_run(self.workspace, "run_missing")
        self.assertEqual(context.exception.error.code, "RUN_NOT_FOUND")

    async def test_original_file_bytes_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "05_General_Schedule.xlsx"
            source.write_bytes(b"source workbook bytes")
            before = source.read_bytes()
            run_id = await self.ready()
            await self.service.approve(self.workspace, run_id)
            self.assertEqual(source.read_bytes(), before)

    async def test_day_cancellation_keeps_cancelled_events_in_preview(self) -> None:
        _, run_id = await self.service.create_run(
            self.workspace,
            CreateHealingRunRequest(
                cancellation_type="day",
                date="2026-07-08",
            ),
        )
        await self.service.process_run(self.workspace, run_id)
        await self.service.approve(self.workspace, run_id)
        preview = await self.service.schedule(self.workspace)
        cancelled = next(
            event for event in preview.events if event.id == "event_cancel"
        )
        self.assertEqual(cancelled.status, "cancelled")

    async def test_export_skeleton_is_explicitly_unavailable(self) -> None:
        result = await self.service.export(self.workspace)
        self.assertEqual(result.status, "not_implemented")
        self.assertIn("not implemented", result.message.casefold())


class ClosingTaskManager:
    def start(self, coroutine) -> None:
        coroutine.close()


class HealingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_service = service()
        api.app.dependency_overrides[get_service] = lambda: self.test_service
        api.app.dependency_overrides[get_task_manager] = ClosingTaskManager
        self.client = TestClient(api.app)
        self.headers = {
            "X-API-Key": "test-secret",
            "X-Workspace-ID": "workspace-route-test",
        }
        self.environment = patch.dict(
            "os.environ", {"SCHEDULER_UI_API_KEY": "test-secret"}
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        api.app.dependency_overrides.clear()
        self.client.close()

    def test_create_route_returns_processing_contract(self) -> None:
        response = self.client.post(
            "/api/healing-runs",
            headers=self.headers,
            json={
                "cancellation_type": "events",
                "event_ids": ["event_cancel"],
            },
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["status"], "processing")
        self.assertTrue(payload["run_id"].startswith("run_"))

    def test_validation_error_uses_stable_contract(self) -> None:
        response = self.client.post(
            "/api/healing-runs",
            headers=self.headers,
            json={"cancellation_type": "events", "event_ids": []},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["schema_version"], "1.0")
        self.assertEqual(
            response.json()["error"]["code"], "INVALID_CANCELLATION"
        )

    def test_workspace_is_required(self) -> None:
        response = self.client.get(
            "/api/schedule",
            headers={"X-API-Key": "test-secret"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_WORKSPACE")


if __name__ == "__main__":
    unittest.main()
