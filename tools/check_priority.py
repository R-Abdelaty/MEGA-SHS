"""Session-priority tool skeleton."""

from langchain.tools import tool


@tool
def check_priority(session_ids: list[str], disruption_details: str) -> str:
    """Check the academic and operational priority of affected sessions."""
    return "TODO: evaluate session priority using university policies."

