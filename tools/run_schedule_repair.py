"""Schedule-repair tool skeleton."""

from langchain.tools import tool


@tool
def run_schedule_repair(disruption_id: str, affected_session_ids: list[str]) -> str:
    """Generate repair options while keeping unaffected sessions unchanged."""
    return "TODO: run the schedule repair engine and return candidate repairs."

