"""Scheduling-disruption reporting tool skeleton."""

from langchain.tools import tool


@tool
def report_disruption(
    disruption_type: str,
    description: str,
    affected_day: str,
    affected_resource_ids: list[str],
) -> str:
    """Record a scheduling disruption and its affected date and resources."""
    return "TODO: create and store a structured disruption report."

