"""Deterministic, display-ready API formatting."""

from __future__ import annotations

from datetime import date

from mega_shs.api_models import (
    ChangeDisplay,
    ProposedActionType,
    ScheduleEventResponse,
    SchedulePosition,
)


def format_date_label(value: date, include_date: bool = False) -> str:
    day_name = value.strftime("%A")
    return f"{day_name}, {value.strftime('%b')} {value.day}" if include_date else day_name


def format_time_range(start_time: str, end_time: str) -> str:
    if not start_time and not end_time:
        return "Time unavailable"
    if not end_time:
        return start_time
    if not start_time:
        return end_time
    return f"{start_time}–{end_time}"


def format_change_title(event: ScheduleEventResponse) -> str:
    return event.name.strip() or "Schedule activity"


def format_change_detail(
    action_type: ProposedActionType | str,
    previous: SchedulePosition,
    proposed: SchedulePosition,
) -> str:
    previous_label = (
        f"{format_date_label(previous.date)} {previous.start_time}"
        if previous.date == proposed.date
        else f"{format_date_label(previous.date, include_date=True)} {previous.start_time}"
    )
    proposed_label = (
        f"{format_date_label(proposed.date)} {proposed.start_time}"
        if previous.date == proposed.date
        else f"{format_date_label(proposed.date, include_date=True)} {proposed.start_time}"
    )
    if previous.room.strip().casefold() != proposed.room.strip().casefold():
        previous_label = f"{previous_label} · {previous.room}"
        proposed_label = f"{proposed_label} · {proposed.room}"
    return f"{previous_label} → {proposed_label}"


def format_status_label(status: str | None) -> str | None:
    if not status:
        return None
    return status.strip().upper()


def format_move_display(
    event: ScheduleEventResponse,
    action_type: ProposedActionType | str,
    previous: SchedulePosition,
    proposed: SchedulePosition,
) -> ChangeDisplay:
    return ChangeDisplay(
        title=format_change_title(event),
        detail=format_change_detail(action_type, previous, proposed),
        status_label=None,
    )


def format_cancellation_display(event: ScheduleEventResponse) -> ChangeDisplay:
    details = [format_status_label("cancelled")]
    if event.student_group:
        details.append(event.student_group)
    if event.room:
        details.append(event.room)
    return ChangeDisplay(
        title=format_change_title(event),
        detail=" · ".join(details),
        status_label="CANCELLED",
    )
