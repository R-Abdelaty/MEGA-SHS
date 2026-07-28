"""Resolve and validate structured agent proposals against the schedule."""

from __future__ import annotations

import hashlib
from datetime import date

from mega_shs.agent_models import AgentHealingResult
from mega_shs.api_models import (
    ProposedActionType,
    ProposedScheduleAction,
    RequestedCancellation,
    ScheduleEventIdentity,
    ScheduleEventResponse,
    SchedulePosition,
)
from mega_shs.formatters import format_move_display
from mega_shs.schedule import NormalizedSchedule


class InvalidProposal(ValueError):
    pass


def _minutes(value: str) -> int:
    try:
        hour, minute = value.split(":")
        result = int(hour) * 60 + int(minute)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidProposal(f"Invalid time value: {value!r}") from exc
    if not 0 <= result < 24 * 60:
        raise InvalidProposal(f"Invalid time value: {value!r}")
    return result


def _position(event: ScheduleEventResponse) -> SchedulePosition:
    return SchedulePosition(
        date=event.date,
        start_time=event.start_time,
        end_time=event.end_time,
        room=event.room,
    )


def _position_key(position: SchedulePosition) -> tuple[str, str, str, str]:
    return (
        position.date.isoformat(),
        position.start_time,
        position.end_time,
        position.room.strip().casefold(),
    )


def _groups(value: str) -> set[str]:
    return {
        item.strip().casefold()
        for item in value.replace(",", ";").split(";")
        if item.strip()
    }


def _overlaps(first: ScheduleEventResponse, second: ScheduleEventResponse) -> bool:
    if first.date != second.date:
        return False
    if _minutes(first.end_time) <= _minutes(second.start_time):
        return False
    if _minutes(second.end_time) <= _minutes(first.start_time):
        return False
    room_conflict = (
        first.room.strip().casefold() == second.room.strip().casefold()
        and first.room.strip().casefold() not in {"", "unassigned"}
    )
    group_conflict = bool(_groups(first.student_group) & _groups(second.student_group))
    return room_conflict or group_conflict


def _related_to_cancellation(
    event: ScheduleEventResponse,
    cancelled_events: list[ScheduleEventResponse],
    cancellation_date: date | None,
) -> bool:
    if not cancelled_events:
        return False
    event_room = event.room.strip().casefold()
    event_groups = _groups(event.student_group)
    resource_related = any(
        (
            event_room
            and event_room == cancelled.room.strip().casefold()
            and event_room != "unassigned"
        )
        or bool(event_groups & _groups(cancelled.student_group))
        for cancelled in cancelled_events
    )
    if not resource_related:
        return False
    if cancellation_date is None:
        return True
    return abs((event.date - cancellation_date).days) <= 21


def _action_id(
    event_id: str,
    action_type: str,
    previous: SchedulePosition,
    proposed: SchedulePosition,
) -> str:
    value = "|".join(
        [
            event_id,
            action_type,
            *_position_key(previous),
            *_position_key(proposed),
        ]
    )
    return f"action_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def validate_agent_result(
    result: AgentHealingResult,
    source_schedule: NormalizedSchedule,
    current_events: list[ScheduleEventResponse],
    cancellation: RequestedCancellation,
) -> list[ProposedScheduleAction]:
    """Validate every move and deterministically build public action objects."""
    events_by_id = {event.id: event for event in current_events}
    authoritative_rooms = {
        room.strip().casefold(): room.strip()
        for room in source_schedule.rooms
        if room.strip()
    }
    metadata_by_source: dict[str, list[str]] = {}
    for normalized in source_schedule.events:
        metadata_by_source.setdefault(normalized.source_event_id, []).append(
            normalized.public.id
        )

    cancelled_events = [
        events_by_id[event_id]
        for event_id in cancellation.event_ids
        if event_id in events_by_id
    ]
    cancelled_ids = set(cancellation.event_ids)
    cancellation_date = cancellation.date
    if cancellation_date is None and cancelled_events:
        dates = {event.date for event in cancelled_events}
        cancellation_date = next(iter(dates)) if len(dates) == 1 else None

    seen_movements: set[tuple[str, str, tuple[str, str, str, str]]] = set()
    moved_event_ids: set[str] = set()
    resolved: list[
        tuple[
            str,
            str,
            ScheduleEventResponse,
            SchedulePosition,
            SchedulePosition,
            str,
        ]
    ] = []

    for move in result.proposed_moves:
        reference = move.event_reference
        event: ScheduleEventResponse | None = None
        if reference.event_id:
            event = events_by_id.get(reference.event_id)
        if event is None and reference.source_event_id:
            candidates = [
                events_by_id[event_id]
                for event_id in metadata_by_source.get(reference.source_event_id, [])
                if event_id in events_by_id
                and events_by_id[event_id].date == move.previous.date
            ]
            if len(candidates) == 1:
                event = candidates[0]
        if event is None:
            raise InvalidProposal("A proposed event could not be resolved.")
        if event.id in cancelled_ids:
            raise InvalidProposal(
                "The agent cannot move an event selected for cancellation."
            )
        if event.status == "cancelled":
            raise InvalidProposal("The agent cannot move an already-cancelled event.")

        supplied_previous = SchedulePosition.model_validate(move.previous.model_dump())
        proposed = SchedulePosition.model_validate(move.proposed.model_dump())
        current_position = _position(event)
        if _position_key(supplied_previous) != _position_key(current_position):
            raise InvalidProposal(
                "A proposed movement does not match the event's current position."
            )
        previous = current_position
        proposed_room_key = proposed.room.strip().casefold()
        current_room_key = event.room.strip().casefold()
        if proposed_room_key == current_room_key:
            proposed = proposed.model_copy(update={"room": event.room})
        else:
            authoritative_room = authoritative_rooms.get(proposed_room_key)
            if authoritative_room is None:
                raise InvalidProposal(
                    "A proposed room does not exist in the active room inventory."
                )
            proposed = proposed.model_copy(update={"room": authoritative_room})
        if _minutes(previous.end_time) <= _minutes(previous.start_time):
            raise InvalidProposal("The previous time range is invalid.")
        if _minutes(proposed.end_time) <= _minutes(proposed.start_time):
            raise InvalidProposal("The proposed time range is invalid.")
        if (
            _minutes(previous.end_time) - _minutes(previous.start_time)
            != _minutes(proposed.end_time) - _minutes(proposed.start_time)
        ):
            raise InvalidProposal("A movement cannot change event duration.")

        action_type = move.action_type
        if action_type == ProposedActionType.MOVE_TIME:
            if previous.date != proposed.date:
                raise InvalidProposal("move_time cannot change the event date.")
            if (
                previous.start_time == proposed.start_time
                and previous.end_time == proposed.end_time
            ):
                raise InvalidProposal("move_time must change the event time.")
        elif action_type == ProposedActionType.MOVE_DATE:
            if previous.date == proposed.date:
                raise InvalidProposal("move_date must change the event date.")
        else:
            raise InvalidProposal(f"Unsupported action type: {action_type}")

        movement_key = (event.id, action_type, _position_key(proposed))
        if movement_key in seen_movements:
            raise InvalidProposal("The agent returned a duplicate movement.")
        if event.id in moved_event_ids:
            raise InvalidProposal(
                "The agent returned contradictory movements for one event."
            )
        if not _related_to_cancellation(event, cancelled_events, cancellation_date):
            raise InvalidProposal(
                "A proposed movement is not related to the selected cancellation."
            )

        seen_movements.add(movement_key)
        moved_event_ids.add(event.id)
        resolved.append(
            (event.id, action_type, event, previous, proposed, move.reason.strip())
        )

    unaffected = [
        event
        for event in current_events
        if event.id not in moved_event_ids
        and event.id not in cancelled_ids
        and event.status != "cancelled"
    ]
    proposed_events: list[ScheduleEventResponse] = []
    actions: list[ProposedScheduleAction] = []
    for event_id, action_type, event, previous, proposed, reason in resolved:
        candidate = event.model_copy(
            update={
                "date": proposed.date,
                "start_time": proposed.start_time,
                "end_time": proposed.end_time,
                "room": proposed.room,
            }
        )
        if any(_overlaps(candidate, existing) for existing in [*unaffected, *proposed_events]):
            raise InvalidProposal(
                "A proposed position conflicts with a room or student group."
            )
        proposed_events.append(candidate)
        actions.append(
            ProposedScheduleAction(
                action_id=_action_id(
                    event_id, action_type, previous, proposed
                ),
                action_type=action_type,
                event_id=event_id,
                event=ScheduleEventIdentity(
                    name=event.name,
                    type=event.type,
                    room=event.room,
                    student_group=event.student_group,
                ),
                previous=previous,
                proposed=proposed,
                reason=reason,
                display=format_move_display(
                    event, action_type, previous, proposed
                ),
            )
        )
    return actions


def revalidate_actions(
    actions: list[ProposedScheduleAction],
    source_schedule: NormalizedSchedule,
    current_events: list[ScheduleEventResponse],
    cancellation: RequestedCancellation,
) -> None:
    result = AgentHealingResult(
        summary="Revalidation",
        proposed_moves=[
            {
                "action_type": action.action_type,
                "event_reference": {"event_id": action.event_id},
                "previous": action.previous.model_dump(),
                "proposed": action.proposed.model_dump(),
                "reason": action.reason,
            }
            for action in actions
        ],
    )
    validate_agent_result(result, source_schedule, current_events, cancellation)
