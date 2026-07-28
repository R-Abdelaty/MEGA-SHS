"""Authenticated ASGI API used by the scheduler UI."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api_contract import (
    ASSESSMENT_TYPES,
    CANCELLATION_REASONS,
    CONFIRMATIONS,
    PERIODS,
    PROBLEM_TYPES,
    TIME_SCOPES,
    URGENCIES,
    catalog,
    resolve_catalog_options,
    resolve_day,
    resolve_period,
    ui_options,
)
from intake_middleware import answer_intake, get_intake, start_intake
from tools.cancel_day import cancel_day
from tools.report_disruption import report_disruption


load_dotenv()
API_PREFIX = "/api/v1"
_PROTOTYPE_REQUESTS: dict[str, dict[str, Any]] = {}
_PROTOTYPE_LOCK = threading.Lock()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DisruptionForm(StrictModel):
    problem_option: int
    day_option: int | None = None
    academic_week: int | None = Field(default=None, ge=1, le=12)
    description: str | None = None
    reason_option: int | None = None
    custom_reason: str | None = None
    resource_catalog: str | None = None
    resource_options: list[int] = Field(default_factory=list)
    resource_values: list[str] = Field(default_factory=list)
    session_options: list[int] = Field(default_factory=list)
    session_values: list[str] = Field(default_factory=list)
    student_group_options: list[int] = Field(default_factory=list)
    student_group_values: list[str] = Field(default_factory=list)
    scope_option: int | None = None
    start_period_option: int | None = None
    end_period_option: int | None = None
    assessment_option: int | None = None
    urgency_option: int | None = None
    corrected_room_capacity: int | None = Field(default=None, gt=0)
    related_repair_id: str | None = None
    confirmation_option: int = 1


class CancelDayPrototypeForm(StrictModel):
    day_option: int
    academic_week: int = Field(ge=1, le=12)
    reason_option: int
    custom_reason: str | None = None
    confirmation_option: int = 1
    maximum_following_weeks: int = Field(default=2, ge=1, le=2)
    result_offset: int = Field(default=0, ge=0)
    result_limit: int = Field(default=50, ge=1, le=100)


class AgentMessage(StrictModel):
    message: str = Field(min_length=1)
    thread_id: str = Field(default="scheduler-ui", min_length=1, max_length=200)


class IntakeAnswer(StrictModel):
    option: int | None = None
    options: list[int] | None = None
    number: int | None = None
    text: str | None = None


def _json_tool_result(raw: Any) -> dict[str, Any]:
    if hasattr(raw, "content"):
        raw = raw.content
    if isinstance(raw, str):
        decoded = json.loads(raw)
    else:
        decoded = raw
    if not isinstance(decoded, dict):
        raise ValueError("Tool response was not a JSON object.")
    return decoded


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    payload: dict[str, Any] = {
        "status": "error",
        "error": {"code": code, "message": message},
    }
    if details is not None:
        payload["error"]["details"] = details
    return JSONResponse(payload, status_code=status_code)


def _authenticate(request: Request) -> JSONResponse | None:
    expected = os.getenv("SCHEDULER_UI_API_KEY", "").strip()
    if not expected:
        return _error(
            503,
            "api_key_not_configured",
            "Set SCHEDULER_UI_API_KEY on the backend before serving the UI.",
        )
    provided = request.headers.get("x-api-key", "")
    if not provided or not secrets.compare_digest(provided, expected):
        return _error(401, "unauthorized", "A valid X-API-Key header is required.")
    return None


async def _body(request: Request, model: type[StrictModel]) -> StrictModel | JSONResponse:
    try:
        return model.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as exc:
        details = exc.errors() if isinstance(exc, ValidationError) else str(exc)
        return _error(422, "invalid_request_body", "The JSON body is invalid.", details)


def _reason(option: int | None, custom: str | None) -> str | None:
    if option is None:
        return None
    if option not in CANCELLATION_REASONS:
        raise ValueError("reason_option must be between 1 and 7")
    if option == 7:
        if not custom or not custom.strip():
            raise ValueError("custom_reason is required when reason_option is 7")
        return custom.strip()
    return CANCELLATION_REASONS[option]


def _resources(form: DisruptionForm) -> list[str]:
    values = [value.strip() for value in form.resource_values if value.strip()]
    if not form.resource_options:
        return values
    inferred = {
        1: "staff",
        2: "rooms",
        3: "equipment",
        7: "staff",
        10: "rooms",
    }.get(form.problem_option)
    catalog_name = form.resource_catalog or inferred
    if not catalog_name:
        raise ValueError("resource_catalog is required for these numeric resource options")
    return [*values, *resolve_catalog_options(catalog_name, form.resource_options)]


def _time_scope(form: DisruptionForm) -> dict[str, Any]:
    if form.problem_option == 4:
        return {"whole_day": True, "start_time": None, "end_time": None}
    if form.scope_option is None:
        return {"whole_day": False, "start_time": None, "end_time": None}
    if form.scope_option not in TIME_SCOPES:
        raise ValueError("scope_option must be between 1 and 3")
    if form.scope_option == 3:
        if form.problem_option == 5:
            raise ValueError("Partial-day cancellation cannot use the full-day scope")
        return {"whole_day": True, "start_time": None, "end_time": None}
    start = resolve_period(form.start_period_option)
    if start is None:
        raise ValueError("start_period_option is required for a period scope")
    end = start if form.scope_option == 1 else resolve_period(form.end_period_option)
    if end is None:
        raise ValueError("end_period_option is required for a period range")
    if form.scope_option == 2 and int(end["period_id"][1:]) < int(start["period_id"][1:]):
        raise ValueError("end_period_option must not precede start_period_option")
    return {"whole_day": False, "start_time": start["start"], "end_time": end["end"]}


def disruption_arguments(form: DisruptionForm) -> dict[str, Any]:
    if form.problem_option not in PROBLEM_TYPES:
        raise ValueError("problem_option must be between 1 and 11")
    if form.confirmation_option not in CONFIRMATIONS:
        raise ValueError("confirmation_option must be 1 or 2")
    if form.confirmation_option == 2:
        raise ValueError("The request was cancelled by the user")
    label, disruption_type = PROBLEM_TYPES[form.problem_option]
    reason = _reason(form.reason_option, form.custom_reason)
    description = (form.description or reason or label).strip()
    if form.assessment_option is not None:
        if form.assessment_option not in ASSESSMENT_TYPES:
            raise ValueError("assessment_option must be between 1 and 3")
        description = f"Unexpected {ASSESSMENT_TYPES[form.assessment_option].casefold()} added. {description}"
    urgency = None
    if form.urgency_option is not None:
        if form.urgency_option not in URGENCIES:
            raise ValueError("urgency_option must be between 1 and 4")
        urgency = URGENCIES[form.urgency_option]
    session_ids = [*form.session_values, *resolve_catalog_options("sessions", form.session_options)]
    group_ids = [
        *form.student_group_values,
        *resolve_catalog_options("student-groups", form.student_group_options),
    ]
    return {
        "disruption_type": disruption_type,
        "description": description,
        "affected_day_or_date": resolve_day(form.day_option),
        "academic_week": form.academic_week,
        "affected_resource_ids": _resources(form),
        "affected_session_ids": session_ids,
        "affected_student_group_ids": group_ids,
        **_time_scope(form),
        "urgency": urgency,
        "corrected_room_capacity": form.corrected_room_capacity,
        "related_repair_id": form.related_repair_id,
    }


async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "api_version": "v1",
            "ui_api_key_configured": bool(os.getenv("SCHEDULER_UI_API_KEY", "").strip()),
            "anthropic_api_key_configured": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        }
    )


async def options_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    return JSONResponse(ui_options())


async def intake_start_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    return JSONResponse(start_intake(), status_code=201)


async def intake_status_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    try:
        return JSONResponse(get_intake(request.path_params["intake_id"]))
    except KeyError:
        return _error(404, "intake_not_found", "The intake session does not exist.")


def _wizard_disruption_form(answers: dict[str, Any]) -> DisruptionForm:
    problem = answers["problem_option"]
    resource_catalog = None
    if problem == 9:
        resource_catalog = {1: "rooms", 2: "equipment"}.get(
            answers.get("resource_catalog_option")
        )
    return DisruptionForm(
        problem_option=problem,
        day_option=answers.get("day_option"),
        academic_week=answers.get("academic_week"),
        description=answers.get("description"),
        reason_option=answers.get("reason_option"),
        custom_reason=answers.get("custom_reason"),
        resource_catalog=resource_catalog,
        resource_options=answers.get("resource_options", []),
        session_options=answers.get("session_options", []),
        student_group_options=answers.get("student_group_options", []),
        scope_option=(1 if problem == 8 else answers.get("scope_option")),
        start_period_option=answers.get("start_period_option"),
        end_period_option=answers.get("end_period_option"),
        assessment_option=answers.get("assessment_option"),
        corrected_room_capacity=answers.get("corrected_room_capacity"),
        related_repair_id=answers.get("related_repair_id"),
        confirmation_option=answers.get("confirmation_option", 1),
    )


async def execute_ready_intake(snapshot: dict[str, Any]) -> dict[str, Any]:
    answers = snapshot["answers"]
    if answers["problem_option"] == 4:
        arguments = {
            "day": resolve_day(answers["day_option"]),
            "academic_week": answers["academic_week"],
            "reason": _reason(answers["reason_option"], answers.get("custom_reason")),
            "cancellation_approved": True,
            "maximum_following_weeks": 2,
            "result_offset": 0,
            "result_limit": 50,
        }
        result = _json_tool_result(await asyncio.to_thread(cancel_day.invoke, arguments))
        prototype_id = result.get("prototype_id")
        if prototype_id:
            with _PROTOTYPE_LOCK:
                _PROTOTYPE_REQUESTS[str(prototype_id)] = arguments
        return result
    arguments = disruption_arguments(_wizard_disruption_form(answers))
    return _json_tool_result(await asyncio.to_thread(report_disruption.invoke, arguments))


async def intake_answer_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    body = await _body(request, IntakeAnswer)
    if isinstance(body, JSONResponse):
        return body
    try:
        snapshot = answer_intake(
            request.path_params["intake_id"],
            body.model_dump(exclude_none=True),
        )
        if snapshot["status"] == "cancelled":
            return JSONResponse(snapshot)
        if snapshot["ready_to_execute"]:
            snapshot["execution"] = await execute_ready_intake(snapshot)
        return JSONResponse(snapshot)
    except KeyError:
        return _error(404, "intake_not_found", "The intake session does not exist.")
    except ValueError as exc:
        return _error(400, "invalid_intake_answer", str(exc))


async def catalog_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    try:
        items = catalog(request.path_params["catalog_name"])
        day = resolve_day(int(request.query_params["day_option"])) if request.query_params.get("day_option") else None
        if day and request.path_params["catalog_name"].casefold() == "sessions":
            items = [item for item in items if str(item.get("day") or "").casefold() == day.casefold()]
        query = request.query_params.get("query", "").strip().casefold()
        if query:
            items = [item for item in items if query in str(item.get("label") or "").casefold()]
        offset = max(0, int(request.query_params.get("offset", "0")))
        limit = min(100, max(1, int(request.query_params.get("limit", "100"))))
    except (ValueError, KeyError) as exc:
        return _error(400, "invalid_catalog_request", str(exc))
    page = items[offset : offset + limit]
    return JSONResponse(
        {
            "status": "success",
            "catalog": request.path_params["catalog_name"],
            "total": len(items),
            "returned": len(page),
            "options": page,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page) < len(items),
                "next_offset": offset + len(page) if offset + len(page) < len(items) else None,
            },
        }
    )


async def report_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    body = await _body(request, DisruptionForm)
    if isinstance(body, JSONResponse):
        return body
    try:
        arguments = disruption_arguments(body)
        result = await asyncio.to_thread(report_disruption.invoke, arguments)
        return JSONResponse(_json_tool_result(result))
    except ValueError as exc:
        return _error(400, "invalid_disruption_options", str(exc))


async def cancel_day_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    body = await _body(request, CancelDayPrototypeForm)
    if isinstance(body, JSONResponse):
        return body
    try:
        if body.confirmation_option not in CONFIRMATIONS:
            raise ValueError("confirmation_option must be 1 or 2")
        if body.confirmation_option == 2:
            return JSONResponse({"status": "cancelled", "prototype_created": False})
        arguments = {
            "day": resolve_day(body.day_option),
            "academic_week": body.academic_week,
            "reason": _reason(body.reason_option, body.custom_reason),
            "cancellation_approved": True,
            "maximum_following_weeks": body.maximum_following_weeks,
            "result_offset": body.result_offset,
            "result_limit": body.result_limit,
        }
        result = _json_tool_result(await asyncio.to_thread(cancel_day.invoke, arguments))
        prototype_id = result.get("prototype_id")
        if prototype_id:
            with _PROTOTYPE_LOCK:
                _PROTOTYPE_REQUESTS[str(prototype_id)] = arguments
        return JSONResponse(result)
    except ValueError as exc:
        return _error(400, "invalid_cancellation_options", str(exc))


async def prototype_day_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    prototype_id = request.path_params["prototype_id"]
    with _PROTOTYPE_LOCK:
        stored = _PROTOTYPE_REQUESTS.get(prototype_id)
    if stored is None:
        return _error(404, "prototype_not_found", "The prototype is not available in this API process.")
    try:
        period_option_text = request.query_params.get("period_option")
        period = resolve_period(int(period_option_text)) if period_option_text else None
        arguments = {
            **stored,
            "display_academic_week": int(request.path_params["academic_week"]),
            "display_day": resolve_day(int(request.path_params["day_option"])),
            "display_period_id": period["period_id"] if period else None,
            "display_offset": max(0, int(request.query_params.get("offset", "0"))),
            "display_limit": min(100, max(1, int(request.query_params.get("limit", "100")))),
        }
        result = _json_tool_result(await asyncio.to_thread(cancel_day.invoke, arguments))
        return JSONResponse(result)
    except ValueError as exc:
        return _error(400, "invalid_day_view_options", str(exc))


async def agent_message_endpoint(request: Request) -> JSONResponse:
    if error := _authenticate(request):
        return error
    body = await _body(request, AgentMessage)
    if isinstance(body, JSONResponse):
        return body
    try:
        from agent import agent

        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": [{"role": "user", "content": body.message}]},
            {"configurable": {"thread_id": body.thread_id}},
        )
        return JSONResponse(
            {
                "status": "success",
                "thread_id": body.thread_id,
                "message": result["messages"][-1].content,
            }
        )
    except Exception as exc:
        return _error(500, "agent_error", "The scheduler agent could not complete the request.", str(exc))


origins = [
    item.strip()
    for item in os.getenv(
        "SCHEDULER_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if item.strip()
]

routes = [
    Route("/health", health, methods=["GET"]),
    Route(f"{API_PREFIX}/options", options_endpoint, methods=["GET"]),
    Route(f"{API_PREFIX}/intake/start", intake_start_endpoint, methods=["POST"]),
    Route(f"{API_PREFIX}/intake/{{intake_id}}", intake_status_endpoint, methods=["GET"]),
    Route(
        f"{API_PREFIX}/intake/{{intake_id}}/answer",
        intake_answer_endpoint,
        methods=["POST"],
    ),
    Route(f"{API_PREFIX}/catalogs/{{catalog_name}}", catalog_endpoint, methods=["GET"]),
    Route(f"{API_PREFIX}/disruptions/report", report_endpoint, methods=["POST"]),
    Route(f"{API_PREFIX}/prototypes/cancel-day", cancel_day_endpoint, methods=["POST"]),
    Route(
        f"{API_PREFIX}/prototypes/{{prototype_id}}/weeks/{{academic_week:int}}/days/{{day_option:int}}",
        prototype_day_endpoint,
        methods=["GET"],
    ),
    Route(f"{API_PREFIX}/agent/messages", agent_message_endpoint, methods=["POST"]),
]

app = Starlette(
    routes=routes,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key"],
        )
    ],
)
