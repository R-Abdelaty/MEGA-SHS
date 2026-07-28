"""LangChain adapter that exposes only validated healing proposals."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Protocol

from openai import (
    APIConnectionError as OpenAIConnectionError,
    APIStatusError as OpenAIStatusError,
    AuthenticationError as OpenAIAuthenticationError,
    PermissionDeniedError as OpenAIPermissionDeniedError,
    RateLimitError as OpenAIRateLimitError,
)
from pydantic import ValidationError

from mega_shs.agent_models import AgentHealingResult
from mega_shs.api_models import RequestedCancellation, ScheduleEventResponse
from mega_shs.errors import AgentExecutionError, AgentOutputError
from mega_shs.schedule import NormalizedSchedule, authoritative_schedule_files


AUTHORITATIVE_SOURCE_PURPOSES = {
    "01_Room_Schedule.xlsx": "Room inventory and room availability",
    "02_Lab_Equipment.xlsx": "Laboratory equipment and features",
    "03_Student_Enrollment.xlsx": "Student groups and course enrollment",
    "04_Course_Catalog.xlsx": "Course and session requirements",
    "05_General_Schedule.xlsx": "General teaching timetable",
    "06_Exam_Schedule.xlsx": "Assessments, quizzes, and final exams",
    "07_Doctor_Schedule_Calendar.xlsx": "Lecturer and doctor availability",
}


def authoritative_source_manifest() -> list[dict[str, str]]:
    return [
        {
            "file_name": path.name,
            "purpose": AUTHORITATIVE_SOURCE_PURPOSES.get(
                path.name, "Authoritative university schedule data"
            ),
        }
        for path in authoritative_schedule_files()
    ]


class HealingAgent(Protocol):
    async def propose(
        self,
        *,
        run_id: str,
        cancellation: RequestedCancellation,
        cancelled_events: list[ScheduleEventResponse],
        source_schedule: NormalizedSchedule,
        retry_feedback: str | None = None,
        rejected_result: AgentHealingResult | None = None,
    ) -> AgentHealingResult: ...


HEALING_SYSTEM_PROMPT = """
You are the proposal-only scheduling engine for MEGA-SHS.

The user has already selected and confirmed the cancellation. Never propose a
cancellation and never call a tool that writes, approves, exports, or applies a
schedule. Read the authoritative Excel schedules with the available read-only
tools and check rooms, instructors, student groups, assessments, and required
equipment before proposing changes.

The request includes an ``authoritative_sources`` manifest containing the exact
fake-data workbook names and their purposes. Use ``get_schedule`` with those
file names. For each proposed movement, consult the general timetable plus every
relevant doctor, room, enrollment, course, exam, and equipment source. Do not
infer availability from the cancelled event payload alone.

Your structured response may contain only move_time and move_date proposals.
Every previous and proposed position must include its room. A proposed room may
differ from the previous room when that room change is required to make the
time/date movement valid. A room change must never be a standalone action: the
time or date must also change. Use move_date when the date changes, including
the complete previous and proposed date/time/room state. Do not return cancel,
add, update, change_room, replace, or delete action types. Do not move an event
selected for cancellation. Prefer the smallest set of movements and do not
return overlapping or contradictory moves. Every proposed event must be
identified by its supplied event_id when known, or by its authoritative source
event ID plus the exact previous date.

Do not generate run IDs, action IDs, timestamps, hashes, statuses, display text,
HTTP errors, internal exceptions, prompts, or reasoning traces. The reason on a
move must be a short, safe validation summary rather than hidden reasoning.
""".strip()


class LangChainHealingAgent:
    """Build a dedicated LangChain graph using ToolStrategy structured output."""

    def __init__(self) -> None:
        self._agent = None

    def _build_agent(self):
        # The existing module owns the configured model and the inspected tool
        # instances. Importing it is delayed so API startup and tests never
        # require a model credential.
        import agent as scheduler_agent_module
        from langchain.agents import create_agent
        from langchain.agents.structured_output import ToolStrategy

        allowed_names = {
            "get_schedule",
            "retrieve_university_policies",
            "check_priority",
            "check_validity",
            "check_lecturer_or_ta_availability",
            "check_room_availability",
            "find_affected_sessions",
        }
        tools = [
            item
            for item in scheduler_agent_module.AGENT_TOOLS
            if getattr(item, "name", "") in allowed_names
        ]
        return create_agent(
            model=scheduler_agent_module.llm,
            system_prompt=HEALING_SYSTEM_PROMPT,
            tools=tools,
            response_format=ToolStrategy(AgentHealingResult),
        )

    async def propose(
        self,
        *,
        run_id: str,
        cancellation: RequestedCancellation,
        cancelled_events: list[ScheduleEventResponse],
        source_schedule: NormalizedSchedule,
        retry_feedback: str | None = None,
        rejected_result: AgentHealingResult | None = None,
    ) -> AgentHealingResult:
        if self._agent is None:
            try:
                self._agent = self._build_agent()
            except Exception as exc:
                raise AgentExecutionError(
                    "The structured scheduling agent could not be initialized."
                ) from exc

        source_ids = {
            item.public.id: item.source_event_id for item in source_schedule.events
        }
        cancellation_payload = cancellation.model_dump(mode="json")
        cancelled_payload = [
            {
                **event.model_dump(mode="json"),
                "source_event_id": source_ids.get(event.id),
            }
            for event in cancelled_events
        ]
        request_payload = {
            "task": "Propose only the movements required to heal this cancellation.",
            "schedule_version": source_schedule.source_version,
            "semester_start_date": os.getenv(
                "SCHEDULE_SEMESTER_START_DATE", "2026-07-05"
            ),
            "authoritative_sources": authoritative_source_manifest(),
            "requested_cancellation": cancellation_payload,
            "cancelled_events": cancelled_payload,
        }
        if retry_feedback:
            request_payload["task"] = (
                "Replace the rejected proposal with a different valid set of "
                "movements for this cancellation."
            )
            request_payload["retry"] = {
                "validation_failure": retry_feedback,
                "rejected_proposal": (
                    rejected_result.model_dump(mode="json")
                    if rejected_result is not None
                    else None
                ),
                "requirements": [
                    "Do not repeat the rejected movement or proposed position.",
                    "Use the read-only tools again before choosing replacements.",
                    "Return only movements that satisfy the validation failure.",
                ],
            }
        message = json.dumps(request_payload, ensure_ascii=False)
        try:
            result = await asyncio.to_thread(
                self._agent.invoke,
                {"messages": [{"role": "user", "content": message}]},
                {"configurable": {"thread_id": f"healing-{run_id}"}},
            )
        except OpenAIAuthenticationError as exc:
            raise AgentExecutionError(
                "The iHQ LiteLLM gateway rejected the configured API key.",
                code="LITELLM_AUTHENTICATION_FAILED",
                public_message=(
                    "The iHQ LiteLLM gateway rejected LITELLM_API_KEY. Check "
                    "the key in the backend .env file and restart the API."
                ),
            ) from exc
        except OpenAIPermissionDeniedError as exc:
            raise AgentExecutionError(
                "The iHQ LiteLLM key cannot use the configured model.",
                code="LITELLM_MODEL_ACCESS_DENIED",
                public_message=(
                    "The iHQ LiteLLM key does not have access to the configured "
                    "model."
                ),
            ) from exc
        except OpenAIRateLimitError as exc:
            raise AgentExecutionError(
                "The iHQ LiteLLM gateway rate-limited the healing request.",
                code="LITELLM_RATE_LIMITED",
                public_message=(
                    "The iHQ LiteLLM gateway rejected the healing run because "
                    "of a rate limit or exhausted key budget. Check /key/info "
                    "and try again."
                ),
            ) from exc
        except OpenAIConnectionError as exc:
            raise AgentExecutionError(
                "The backend could not connect to the iHQ LiteLLM gateway.",
                code="LITELLM_CONNECTION_FAILED",
                public_message=(
                    "The backend could not connect to the iHQ LiteLLM gateway. "
                    "Check internet, firewall, VPN, or proxy access and try "
                    "again."
                ),
            ) from exc
        except OpenAIStatusError as exc:
            raise AgentExecutionError(
                f"The iHQ LiteLLM gateway returned HTTP {exc.status_code}.",
                code="LITELLM_REQUEST_FAILED",
                public_message=(
                    "The iHQ LiteLLM gateway could not complete the model "
                    f"request (HTTP {exc.status_code})."
                ),
            ) from exc
        except Exception as exc:
            raise AgentExecutionError(
                "The scheduling agent could not complete the healing run."
            ) from exc

        structured = result.get("structured_response") if isinstance(result, dict) else None
        try:
            return (
                structured
                if isinstance(structured, AgentHealingResult)
                else AgentHealingResult.model_validate(structured)
            )
        except (ValidationError, TypeError) as exc:
            raise AgentOutputError(
                "The scheduling agent returned invalid structured output."
            ) from exc


class DeterministicNoOpHealingAgent:
    """Explicit development/QA adapter; never selected in production by default."""

    async def propose(
        self,
        *,
        run_id: str,
        cancellation: RequestedCancellation,
        cancelled_events: list[ScheduleEventResponse],
        source_schedule: NormalizedSchedule,
        retry_feedback: str | None = None,
        rejected_result: AgentHealingResult | None = None,
    ) -> AgentHealingResult:
        return AgentHealingResult(
            summary=(
                "The selected cancellation can be previewed without moving "
                "additional schedule activities."
            ),
            proposed_moves=[],
        )


def configured_healing_agent() -> HealingAgent:
    if os.getenv("MEGA_SHS_AGENT_MODE", "").strip().casefold() == "deterministic":
        return DeterministicNoOpHealingAgent()
    return LangChainHealingAgent()
