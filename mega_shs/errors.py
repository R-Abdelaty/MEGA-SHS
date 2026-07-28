"""Controlled exceptions translated to the public API error contract."""

from __future__ import annotations

from typing import Any

from mega_shs.api_models import ApiError


class ApiContractError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = ApiError(code=code, message=message, details=details)


class AgentOutputError(Exception):
    """The model did not produce a valid AgentHealingResult."""


class AgentExecutionError(Exception):
    """The model or agent graph failed before producing a result."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AGENT_EXECUTION_FAILED",
        public_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = public_message or (
            "The scheduling agent could not complete the healing run."
        )


class ToolExecutionError(Exception):
    """A scheduling tool failed during an agent run."""
