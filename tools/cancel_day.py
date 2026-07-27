"""University-day cancellation tool skeleton."""

from langchain.tools import tool


@tool
def cancel_day(day: str, reason: str, cancellation_approved: bool) -> str:
    """Cancel a given day and compensate its sessions after completing every required check."""
    return "TODO: cancel the day, run all checks, and generate compensation sessions."

