"""Compact terminal presentation for structured scheduler tool results."""

from __future__ import annotations

from typing import Any


def _display_status(value: Any) -> str:
    return str(value or "unknown").replace("_", " ").upper()


def _prototype_result(result: dict[str, Any]) -> str:
    timetable = result.get("prototype_timetable") or {}
    cancelled = result.get("cancelled_scope") or {}
    day_views = timetable.get("day_views") or []
    lines = ["STATUS", _display_status(result.get("status")), "", "SUMMARY"]
    lines.append(str(result.get("summary") or "Prototype timetable generated."))
    if result.get("prototype_id"):
        lines.append(f"Prototype ID: {result['prototype_id']}")
    if cancelled:
        lines.append(
            f"Cancelled: {cancelled.get('day', 'Unknown day')}, "
            f"academic week {cancelled.get('academic_week', 'unknown')}"
        )
        lines.append(
            f"Affected sessions: {cancelled.get('affected_session_count', 0)}"
        )
    lines.append(
        f"Compensation assigned: {timetable.get('total_compensation_sessions', 0)}"
    )
    lines.append(
        f"Unassigned sessions: {result.get('unassigned_session_count', 0)}"
    )
    lines.extend(["", "COMPENSATION DAYS"])
    compensation_days = [item for item in day_views if item.get("has_compensation")]
    if compensation_days:
        for item in compensation_days:
            lines.append(
                f"- Week {item.get('academic_week')}, {item.get('day')}: "
                f"{item.get('compensation_session_count', 0)} new compensation, "
                f"{item.get('normal_session_count', 0)} normal"
            )
    else:
        lines.append("- None")
    lines.extend(["", "SAFETY"])
    lines.append(
        "Source schedule modified: "
        + ("Yes" if result.get("source_files_modified") else "No")
    )
    lines.append("Prototype only; no schedule was approved or applied.")
    required_action = result.get("required_action")
    if required_action:
        lines.extend(["", "NEXT ACTION", str(required_action)])
    return "\n".join(lines)


def format_console_result(result: dict[str, Any]) -> str:
    """Show a useful summary while leaving full structured data to the UI API."""
    if result.get("prototype_timetable") is not None:
        return _prototype_result(result)
    lines = ["STATUS", _display_status(result.get("status"))]
    summary = result.get("summary")
    if summary:
        lines.extend(["", "SUMMARY", str(summary)])
    report = result.get("disruption_report") or {}
    if report.get("disruption_id"):
        lines.extend(["", "REFERENCE", f"Disruption ID: {report['disruption_id']}"])
    if "source_files_modified" in result:
        lines.extend(
            [
                "",
                "SAFETY",
                "Source schedule modified: "
                + ("Yes" if result.get("source_files_modified") else "No"),
            ]
        )
    required_action = result.get("required_action")
    if required_action:
        lines.extend(["", "NEXT ACTION", str(required_action)])
    return "\n".join(lines)
