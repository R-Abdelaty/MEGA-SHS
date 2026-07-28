"""One-question-at-a-time numeric intake workflow for the scheduler UI."""

from __future__ import annotations

import copy
import secrets
import threading
from typing import Any

from api_contract import (
    ASSESSMENT_TYPES,
    CONFIRMATIONS,
    DAYS,
    PERIODS,
    PROBLEM_TYPES,
    TIME_SCOPES,
    catalog,
)


_STATES: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
RESOURCE_CATALOG_OPTIONS = {
    1: ("Rooms", "rooms"),
    2: ("Equipment", "equipment"),
}


def _static_options(mapping: dict[int, Any]) -> list[dict[str, Any]]:
    result = []
    for option, value in mapping.items():
        if isinstance(value, tuple):
            label, backend_value = value
            result.append({"option": option, "label": label, "value": backend_value})
        elif isinstance(value, dict):
            result.append({"option": option, **value})
        else:
            result.append({"option": option, "label": str(value), "value": value})
    return result


def _catalog_name(problem_option: int, answers: dict[str, Any]) -> str | None:
    if problem_option in {1, 7}:
        return "staff"
    if problem_option in {2, 10}:
        return "rooms"
    if problem_option == 3:
        return "equipment"
    if problem_option == 6:
        return "sessions"
    if problem_option == 8:
        return "student-groups"
    if problem_option == 9:
        selected = answers.get("resource_catalog_option")
        return RESOURCE_CATALOG_OPTIONS.get(selected, (None, None))[1]
    return None


def _catalog_question(
    key: str,
    prompt: str,
    catalog_name: str,
    *,
    multiple: bool,
    day: str | None = None,
) -> dict[str, Any]:
    items = catalog(catalog_name)
    if catalog_name == "sessions" and day:
        items = [
            item
            for item in items
            if str(item.get("day") or "").casefold() == day.casefold()
        ]
    return {
        "key": key,
        "prompt": prompt,
        "input_type": "multi_select" if multiple else "single_select",
        "options": copy.deepcopy(items[:100]),
        "catalog": {
            "name": catalog_name,
            "total_options": len(items),
            "returned_options": min(100, len(items)),
            "has_more": len(items) > 100,
            "endpoint": f"/api/v1/catalogs/{catalog_name}",
        },
    }


def _question(key: str, answers: dict[str, Any]) -> dict[str, Any]:
    day = DAYS.get(answers.get("day_option"))
    if key == "day_option":
        return {
            "key": key,
            "prompt": "Select the affected day.",
            "input_type": "single_select",
            "options": _static_options(DAYS),
        }
    if key == "academic_week":
        return {
            "key": key,
            "prompt": "Select the affected academic week.",
            "input_type": "single_select",
            "options": [
                {"option": week, "label": f"Week {week}", "value": week}
                for week in range(1, 13)
            ],
        }
    if key == "problem_option":
        return {
            "key": key,
            "prompt": "Select the scheduling problem.",
            "input_type": "single_select",
            "options": _static_options(PROBLEM_TYPES),
        }
    if key == "resource_options":
        problem = answers["problem_option"]
        name = _catalog_name(problem, answers)
        if not name:
            raise ValueError("No resource catalog is available for the selected problem.")
        prompts = {
            "staff": "Select the unavailable doctor or teaching assistant.",
            "rooms": "Select the unavailable room or laboratory.",
            "equipment": "Select the unavailable equipment.",
        }
        return _catalog_question(
            key,
            prompts[name],
            name,
            multiple=problem in {2, 3, 9},
            day=day,
        )
    if key == "session_options":
        return _catalog_question(
            key,
            "Select every cancelled session.",
            "sessions",
            multiple=True,
            day=day,
        )
    if key == "student_group_options":
        return _catalog_question(
            key,
            "Select every student group affected by the assessment.",
            "student-groups",
            multiple=True,
        )
    if key == "resource_catalog_option":
        return {
            "key": key,
            "prompt": "Select the type of resource occupied by the university event.",
            "input_type": "single_select",
            "options": _static_options(RESOURCE_CATALOG_OPTIONS),
        }
    if key == "scope_option":
        options = _static_options(TIME_SCOPES)
        if answers.get("problem_option") == 5:
            options = [item for item in options if item["option"] in {1, 2}]
        return {
            "key": key,
            "prompt": "Select how long the problem lasts.",
            "input_type": "single_select",
            "options": options,
        }
    if key in {"start_period_option", "end_period_option"}:
        return {
            "key": key,
            "prompt": (
                "Select the affected period."
                if key == "start_period_option" and answers.get("scope_option") == 1
                else "Select the first affected period."
                if key == "start_period_option"
                else "Select the last affected period."
            ),
            "input_type": "single_select",
            "options": _static_options(PERIODS),
        }
    if key == "assessment_option":
        return {
            "key": key,
            "prompt": "Select the added assessment type.",
            "input_type": "single_select",
            "options": _static_options(ASSESSMENT_TYPES),
        }
    if key == "corrected_room_capacity":
        return {
            "key": key,
            "prompt": "Enter the corrected room capacity.",
            "input_type": "positive_integer",
        }
    if key == "related_repair_id":
        return {"key": key, "prompt": "Enter the rejected repair ID.", "input_type": "text"}
    if key == "description":
        return {"key": key, "prompt": "Enter the rejection reason.", "input_type": "text"}
    if key == "confirmation_option":
        return {
            "key": key,
            "prompt": "Confirm the request.",
            "input_type": "single_select",
            "options": _static_options(CONFIRMATIONS),
        }
    raise ValueError(f"Unknown intake question: {key}")


def _problem_steps(answers: dict[str, Any]) -> list[str]:
    problem = answers["problem_option"]
    if problem in {1, 2, 3, 7}:
        steps = ["resource_options", "scope_option"]
        scope = answers.get("scope_option")
        if scope in {1, 2}:
            steps.append("start_period_option")
        if scope == 2:
            steps.append("end_period_option")
        return [*steps, "confirmation_option"]
    if problem == 4:
        return ["confirmation_option"]
    if problem == 5:
        steps = ["scope_option"]
        scope = answers.get("scope_option")
        if scope in {1, 2}:
            steps.append("start_period_option")
        if scope == 2:
            steps.append("end_period_option")
        return [*steps, "confirmation_option"]
    if problem == 6:
        return ["session_options", "confirmation_option"]
    if problem == 8:
        return [
            "assessment_option",
            "start_period_option",
            "student_group_options",
            "confirmation_option",
        ]
    if problem == 9:
        steps = ["resource_catalog_option"]
        if "resource_catalog_option" in answers:
            steps.extend(["resource_options", "scope_option"])
            scope = answers.get("scope_option")
            if scope in {1, 2}:
                steps.append("start_period_option")
            if scope == 2:
                steps.append("end_period_option")
        return [*steps, "confirmation_option"]
    if problem == 10:
        return ["resource_options", "corrected_room_capacity", "confirmation_option"]
    if problem == 11:
        return ["related_repair_id", "description", "confirmation_option"]
    raise ValueError("problem_option must be between 1 and 11")


def _next_key(answers: dict[str, Any]) -> str | None:
    for key in ("day_option", "academic_week", "problem_option"):
        if key not in answers:
            return key
    for key in _problem_steps(answers):
        if key not in answers:
            return key
    return None


def _validate_answer(key: str, answer: dict[str, Any], answers: dict[str, Any]) -> Any:
    if key in {"related_repair_id", "description"}:
        value = str(answer.get("text") or "").strip()
        if not value:
            raise ValueError("A nonempty text value is required.")
        return value
    if key == "corrected_room_capacity":
        value = answer.get("number")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("A positive integer is required.")
        return value
    if key in {"resource_options", "session_options", "student_group_options"}:
        values = answer.get("options")
        if values is None and answer.get("option") is not None:
            values = [answer["option"]]
        if not isinstance(values, list) or not values or any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise ValueError("Select at least one numeric option.")
        catalog_name = (
            "sessions"
            if key == "session_options"
            else "student-groups"
            if key == "student_group_options"
            else _catalog_name(answers["problem_option"], answers)
        )
        valid = {item["option"] for item in catalog(catalog_name or "")}
        if any(value not in valid for value in values):
            raise ValueError("One or more selected catalog option numbers are invalid.")
        return list(dict.fromkeys(values))
    value = answer.get("option")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("A numeric option is required.")
    valid_options: set[int]
    if key == "day_option":
        valid_options = set(DAYS)
    elif key == "academic_week":
        valid_options = set(range(1, 13))
    elif key == "problem_option":
        valid_options = set(PROBLEM_TYPES)
    elif key == "resource_catalog_option":
        valid_options = set(RESOURCE_CATALOG_OPTIONS)
    elif key == "scope_option":
        valid_options = {1, 2} if answers.get("problem_option") == 5 else set(TIME_SCOPES)
    elif key in {"start_period_option", "end_period_option"}:
        valid_options = set(PERIODS)
    elif key == "assessment_option":
        valid_options = set(ASSESSMENT_TYPES)
    elif key == "confirmation_option":
        valid_options = set(CONFIRMATIONS)
    else:
        raise ValueError(f"No validator exists for {key}.")
    if value not in valid_options:
        raise ValueError(f"Option {value} is not valid for {key}.")
    if key == "end_period_option":
        start = answers.get("start_period_option")
        if isinstance(start, int) and value < start:
            raise ValueError("The last period cannot precede the first period.")
    return value


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    next_key = _next_key(state["answers"]) if state["status"] == "collecting" else None
    response = {
        "status": state["status"],
        "intake_id": state["intake_id"],
        "answers": copy.deepcopy(state["answers"]),
        "question": _question(next_key, state["answers"]) if next_key else None,
        "ready_to_execute": state["status"] == "ready",
    }
    return response


def start_intake() -> dict[str, Any]:
    intake_id = "INTAKE-" + secrets.token_hex(8).upper()
    state = {"intake_id": intake_id, "status": "collecting", "answers": {}}
    with _LOCK:
        _STATES[intake_id] = state
    return _snapshot(state)


def get_intake(intake_id: str) -> dict[str, Any]:
    with _LOCK:
        state = _STATES.get(intake_id)
        if state is None:
            raise KeyError(intake_id)
        return _snapshot(copy.deepcopy(state))


def answer_intake(intake_id: str, answer: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        state = _STATES.get(intake_id)
        if state is None:
            raise KeyError(intake_id)
        if state["status"] != "collecting":
            raise ValueError("This intake is no longer accepting answers.")
        key = _next_key(state["answers"])
        if key is None:
            raise ValueError("No question is waiting for an answer.")
        state["answers"][key] = _validate_answer(key, answer, state["answers"])
        if key == "confirmation_option":
            state["status"] = "ready" if state["answers"][key] == 1 else "cancelled"
        return _snapshot(copy.deepcopy(state))


def clear_intakes() -> None:
    """Test helper; production callers should not discard active UI state."""
    with _LOCK:
        _STATES.clear()


def format_console_question(question: dict[str, Any]) -> str:
    """Render one middleware question for the temporary terminal interface."""
    lines = [str(question["prompt"])]
    options = question.get("options") or []
    for item in options:
        lines.append(f"  {item['option']}. {item.get('label', item.get('value', ''))}")
    catalog_info = question.get("catalog")
    if catalog_info and catalog_info.get("has_more"):
        lines.append(
            "  "
            f"Showing {catalog_info['returned_options']} of "
            f"{catalog_info['total_options']} options."
        )
    input_type = question["input_type"]
    if input_type == "multi_select":
        lines.append("Enter one or more option numbers separated by commas.")
    elif input_type == "single_select":
        lines.append("Enter one option number.")
    elif input_type == "positive_integer":
        lines.append("Enter a positive whole number.")
    else:
        lines.append("Enter the requested text.")
    return "\n".join(lines)


def parse_console_answer(question: dict[str, Any], raw_value: str) -> dict[str, Any]:
    """Convert temporary terminal input into the same payload used by the UI API."""
    value = raw_value.strip()
    if not value:
        raise ValueError("An answer is required.")
    input_type = question["input_type"]
    if input_type == "text":
        return {"text": value}
    if input_type == "positive_integer":
        try:
            return {"number": int(value)}
        except ValueError as exc:
            raise ValueError("Enter a positive whole number.") from exc
    if input_type == "multi_select":
        try:
            return {
                "options": [int(part.strip()) for part in value.split(",") if part.strip()]
            }
        except ValueError as exc:
            raise ValueError("Enter option numbers separated by commas.") from exc
    try:
        return {"option": int(value)}
    except ValueError as exc:
        raise ValueError("Enter one of the displayed option numbers.") from exc
