"""Schedule-repair approval tool skeleton."""

from langchain.tools import tool


@tool
def approve_repair(repair_id: str, approved_by: str, confirmation: bool) -> str:
    """Approve an exact repair only after validation and explicit confirmation.

    Call this tool only when the exact repair identified by ``repair_id`` has
    already passed ``check_validity`` with ``validation_status == "valid"`` and
    ``validation_complete == true``, and the user explicitly approved that exact
    repair. ``approved_by`` must identify the approving user and ``confirmation``
    must be true. Do not call for a different, modified, partially validated, or
    merely proposed repair. A repair is not approved unless this tool confirms
    success.
    """
    return "TODO: record approval for the exact validated repair."

