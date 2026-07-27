"""Mandatory schedule-validity tool skeleton."""

from langchain.tools import tool


@tool
def check_validity(schedule_version: str) -> str:
    """Perform the mandatory highest-priority validation and detect every schedule problem."""
    return "TODO: run all conflict, capacity, accessibility, resource, and policy checks."

