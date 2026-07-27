"""Lecturer and TA availability tool skeleton."""

from langchain.tools import tool


@tool
def check_lecturer_or_ta_availability(
    staff_ids: list[str],
    proposed_day: str,
    proposed_start: str,
    proposed_end: str,
) -> str:
    """Check lecturer or TA availability against their schedules."""
    return "TODO: check the proposed time against every lecturer or TA schedule."

