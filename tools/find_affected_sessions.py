"""Affected-session detection tool skeleton."""

from langchain.tools import tool


@tool
def find_affected_sessions(disruption_id: str) -> str:
    """Find every session directly or indirectly affected by a disruption."""
    return "TODO: identify the smallest complete affected-session scope."

