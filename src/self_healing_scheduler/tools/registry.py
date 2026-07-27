"""Central tool dispatch and write-operation guard."""

from __future__ import annotations

from typing import Any, Callable

from .definitions import TOOL_SPEC_BY_NAME, TOOL_SPECS, ToolSpec
from .handlers import SchedulerToolHandlers


class ToolRegistry:
    def __init__(self, handlers: SchedulerToolHandlers, *, allow_writes: bool = False) -> None:
        self._handlers = handlers
        self.allow_writes = allow_writes

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return TOOL_SPECS

    def model_tools(self) -> list[dict[str, Any]]:
        return [spec.as_model_tool() for spec in self.specs]

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = TOOL_SPEC_BY_NAME.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool: {name}")
        if spec.write_operation and not self.allow_writes:
            raise PermissionError(
                f"Write tool '{name}' is disabled. Enable it only inside an approved workflow."
            )

        handler: Callable[..., Any] = getattr(self._handlers, name)
        return handler(**arguments)

