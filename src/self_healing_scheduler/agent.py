"""Provider-neutral orchestration skeleton for the scheduler agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()


class ModelClient(Protocol):
    """Implement this adapter for the chosen model provider."""

    def complete(
        self,
        *,
        system_prompt: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> ModelTurn:
        ...


class SchedulerAgent:
    def __init__(
        self,
        model: ModelClient,
        tools: ToolRegistry,
        *,
        system_prompt: str | None = None,
        max_steps: int = 20,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt or load_system_prompt()
        self.max_steps = max_steps

    def run(self, request: str) -> str:
        """Run a minimal tool loop; provider-specific streaming belongs in an adapter."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": request}]

        for _ in range(self.max_steps):
            turn = self.model.complete(
                system_prompt=self.system_prompt,
                messages=messages,
                tools=self.tools.model_tools(),
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text,
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in turn.tool_calls
                    ],
                }
            )

            if not turn.tool_calls:
                return turn.text

            for call in turn.tool_calls:
                try:
                    output = {"ok": True, "result": self.tools.execute(call.name, call.arguments)}
                except (KeyError, PermissionError, NotImplementedError, TypeError, ValueError) as error:
                    output = {"ok": False, "error": type(error).__name__, "message": str(error)}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": json.dumps(output, default=str),
                    }
                )

        raise RuntimeError(f"Agent exceeded the {self.max_steps}-step safety limit.")


def load_system_prompt() -> str:
    prompt_path = Path(__file__).with_name("prompts") / "system_prompt.md"
    return prompt_path.read_text(encoding="utf-8")

