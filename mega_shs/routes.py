"""FastAPI routes for the validated healing-run contract."""

from __future__ import annotations

import os
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from mega_shs.agent_adapter import configured_healing_agent
from mega_shs.api_models import (
    ApiErrorResponse,
    ApproveHealingRunResponse,
    ChangeHistoryResponse,
    CreateHealingRunRequest,
    CreateHealingRunResponse,
    ExportScheduleResponse,
    HealingRunResponse,
    RejectHealingRunResponse,
    ScheduleResponse,
)
from mega_shs.errors import ApiContractError
from mega_shs.exporter import PlaceholderScheduleExporter
from mega_shs.repository import InMemoryHealingRunRepository
from mega_shs.schedule import ExcelScheduleLoader
from mega_shs.service import (
    BackgroundTaskManager,
    HealingRunService,
    max_stored_runs,
)


WORKSPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
repository = InMemoryHealingRunRepository(max_runs=max_stored_runs())
task_manager = BackgroundTaskManager()
service = HealingRunService(
    repository=repository,
    schedule_loader=ExcelScheduleLoader(),
    agent=configured_healing_agent(),
    exporter=PlaceholderScheduleExporter(),
)


def get_service() -> HealingRunService:
    return service


def get_task_manager() -> BackgroundTaskManager:
    return task_manager


async def get_workspace_id(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_workspace_id: Annotated[
        str | None, Header(alias="X-Workspace-ID")
    ] = None,
) -> str:
    expected = os.getenv("SCHEDULER_UI_API_KEY", "").strip()
    if not expected:
        raise ApiContractError(
            503,
            "API_KEY_NOT_CONFIGURED",
            "The scheduler UI API key is not configured.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise ApiContractError(
            401,
            "UNAUTHORIZED",
            "A valid X-API-Key header is required.",
        )
    workspace_id = (x_workspace_id or "").strip()
    if not WORKSPACE_PATTERN.fullmatch(workspace_id):
        raise ApiContractError(
            400,
            "INVALID_WORKSPACE",
            "A valid X-Workspace-ID header is required.",
        )
    return workspace_id


router = APIRouter(
    prefix="/api",
    dependencies=[],
    responses={
        400: {"model": ApiErrorResponse},
        401: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
        503: {"model": ApiErrorResponse},
    },
)


@router.post(
    "/healing-runs",
    response_model=CreateHealingRunResponse,
    status_code=202,
    tags=["healing-runs"],
)
async def create_healing_run(
    request: CreateHealingRunRequest,
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
    tasks: Annotated[BackgroundTaskManager, Depends(get_task_manager)],
) -> CreateHealingRunResponse:
    response, run_id = await healing_service.create_run(workspace_id, request)
    tasks.start(healing_service.process_run(workspace_id, run_id))
    return response


@router.get(
    "/healing-runs/{run_id}",
    response_model=HealingRunResponse,
    tags=["healing-runs"],
)
async def get_healing_run(
    run_id: str,
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> HealingRunResponse:
    return await healing_service.get_run(workspace_id, run_id)


@router.post(
    "/healing-runs/{run_id}/approve",
    response_model=ApproveHealingRunResponse,
    tags=["healing-runs"],
)
async def approve_healing_run(
    run_id: str,
    response: Response,
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> ApproveHealingRunResponse:
    result = await healing_service.approve(workspace_id, run_id)
    if result.status == "stale":
        response.status_code = 409
    return result


@router.post(
    "/healing-runs/{run_id}/reject",
    response_model=RejectHealingRunResponse,
    tags=["healing-runs"],
)
async def reject_healing_run(
    run_id: str,
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> RejectHealingRunResponse:
    return await healing_service.reject(workspace_id, run_id)


@router.get(
    "/change-history",
    response_model=ChangeHistoryResponse,
    tags=["schedule"],
)
async def get_change_history(
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> ChangeHistoryResponse:
    return await healing_service.history(workspace_id)


@router.get(
    "/schedule",
    response_model=ScheduleResponse,
    tags=["schedule"],
)
async def get_schedule(
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> ScheduleResponse:
    return await healing_service.schedule(workspace_id)


@router.post(
    "/schedule/export",
    response_model=ExportScheduleResponse,
    status_code=501,
    tags=["schedule"],
    responses={501: {"model": ExportScheduleResponse}},
)
async def export_schedule(
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    healing_service: Annotated[HealingRunService, Depends(get_service)],
) -> ExportScheduleResponse:
    return await healing_service.export(workspace_id)

