"""Room-availability tool skeleton."""

from langchain.tools import tool


@tool
def check_room_availability(
    room_ids: list[str],
    proposed_day: str,
    proposed_start: str,
    proposed_end: str,
) -> str:
    """Check room availability and suitability against room schedules."""
    return "TODO: check room time, capacity, equipment, accessibility, and suitability."

