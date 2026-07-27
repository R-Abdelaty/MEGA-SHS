"""Backend handler placeholders.

Replace these methods with timetable, room-booking, solver, approval, and
calendar integrations. Keep side effects out of read/analyse handlers.
"""

from __future__ import annotations

from typing import Any


class SchedulerToolHandlers:
    """Deliberately unimplemented integration boundary."""

    def load_schedule(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect the authoritative timetable data source.")

    def load_constraints(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect university policies and session requirements.")

    def get_resource_availability(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect availability, rooms, equipment, and support data.")

    def find_affected_scope(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Implement the disruption dependency analyser.")

    def generate_repair_candidates(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect the selected scheduling optimization engine.")

    def validate_repair_candidate(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Implement authoritative hard/soft constraint validation.")

    def calculate_repair_impact(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Implement people, fairness, accessibility, and sustainability metrics.")

    def record_change_decision(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect the approval and audit workflow.")

    def apply_schedule_patch(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Implement transactional timetable updates with revision checks.")

    def publish_schedule_changes(self, **arguments: Any) -> dict[str, Any]:
        raise NotImplementedError("Connect timetable, email, portal, and calendar publishers.")

