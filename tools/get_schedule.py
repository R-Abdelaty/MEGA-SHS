"""Schedule-file loading tool skeleton."""

from langchain.tools import tool


@tool
def get_schedule(uploaded_file_path: str) -> str:
    """Load a university schedule from an uploaded Excel or PDF file."""
    return "TODO: extract and structure the uploaded Excel or PDF schedule."

