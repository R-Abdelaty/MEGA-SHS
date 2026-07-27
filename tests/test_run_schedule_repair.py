"""Deterministic tests for the schedule repair orchestrator."""

from __future__ import annotations

import importlib
import json
import unittest
from unittest.mock import patch


repair_module = importlib.import_module("tools.run_schedule_repair")
run_schedule_repair = repair_module.run_schedule_repair


class FakeTool:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def invoke(self, arguments):
        self.calls.append(arguments)
        response = self.responder(arguments) if callable(self.responder) else self.responder
        return json.dumps(response)


def schedule_payload(rows: list[dict]) -> dict:
    return {
        "status": "ok",
        "extraction": {
            "matching_rows_found": len(rows),
            "matching_rows_returned": len(rows),
            "has_more": False,
            "next_row_offset": None,
            "sheets": [
                {
                    "name": "Semester Timetable",
                    "tables": [
                        {
                            "name": "Schedule",
                            "rows": [
                                {"excel_row": index + 2, "values": row}
                                for index, row in enumerate(rows)
                            ],
                        }
                    ],
                }
            ],
        },
    }


def affected_session(
    key: str = "S1",
    *,
    day: str = "Monday",
    start: str = "08:30",
    end: str = "10:00",
    room: str = "R1",
    group: str = "G1",
    staff: str = "D1",
    session_type: str = "Lecture",
) -> dict:
    return {
        "affected_session_key": key,
        "session_id": key,
        "course_id": f"COURSE-{key}",
        "course_name": f"Course {key}",
        "session_type": session_type,
        "day": day,
        "start": start,
        "end": end,
        "period": "P1",
        "week": "Weeks 1-12",
        "room": room,
        "room_type": "Lecture Hall",
        "expected_students": 30,
        "student_groups": group,
        "instructor_id": staff,
    }


def normalized_report(disruption_type: str, *, day: str = "Monday") -> dict:
    return {
        "status": "success",
        "report_complete": True,
        "disruption_report": {
            "disruption_id": "DSP-TEST000001",
            "disruption_type": disruption_type,
            "description": "Confirmed test disruption.",
            "affected_resource_ids": ["R1"] if disruption_type == "room_closed" else [],
            "scope": {
                "affected_day": day,
                "affected_date": None,
                "academic_week": 1,
                "whole_day": disruption_type == "day_cancelled",
                "start_time": None if disruption_type == "day_cancelled" else "08:30",
                "end_time": None if disruption_type == "day_cancelled" else "10:00",
            },
        },
    }


def available_staff(_arguments: dict) -> dict:
    return {
        "status": "success",
        "all_available": True,
        "available_staff_ids": ["D1"],
        "unavailable_staff_ids": [],
        "unknown_staff_ids": [],
        "staff_results": [],
    }


def available_rooms(_arguments: dict) -> dict:
    return {
        "status": "success",
        "available_room_count": 2,
        "available_rooms": [
            {
                "room": "R1",
                "type": "Lecture Hall",
                "capacity": 30,
                "features": None,
            },
            {
                "room": "R2",
                "type": "Lecture Hall",
                "capacity": 35,
                "features": None,
            },
        ],
    }


class RunScheduleRepairTests(unittest.TestCase):
    def invoke_with_tools(self, arguments: dict, rows: list[dict], **overrides):
        tools = {
            "get_schedule": FakeTool(schedule_payload(rows)),
            "check_lecturer_or_ta_availability": FakeTool(available_staff),
            "check_room_availability": FakeTool(available_rooms),
            "find_affected_sessions": FakeTool({}),
            "check_priority": FakeTool({}),
        }
        tools.update(overrides)
        with (
            patch.object(repair_module, "get_schedule", tools["get_schedule"]),
            patch.object(
                repair_module,
                "check_lecturer_or_ta_availability",
                tools["check_lecturer_or_ta_availability"],
            ),
            patch.object(
                repair_module,
                "check_room_availability",
                tools["check_room_availability"],
            ),
            patch.object(
                repair_module,
                "find_affected_sessions",
                tools["find_affected_sessions"],
            ),
            patch.object(repair_module, "check_priority", tools["check_priority"]),
        ):
            result = json.loads(run_schedule_repair.invoke(arguments))
        return result, tools

    def test_room_closure_keeps_time_and_changes_only_room(self) -> None:
        affected = affected_session()
        original_booking_conflict = FakeTool(
            {
                "status": "success",
                "all_available": False,
                "unknown_staff_ids": [],
                "staff_results": [
                    {
                        "staff_id": "D1",
                        "available": False,
                        "conflicts": [
                            {
                                "day": "Monday",
                                "start": "08:30",
                                "end": "10:00",
                                "course_id": "COURSE-S1",
                                "course_name": "Course S1",
                                "room": "R1",
                            }
                        ],
                    }
                ],
            }
        )
        result, tools = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["S1"],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00", "period": "P1"}
                ],
            },
            [affected],
            check_lecturer_or_ta_availability=original_booking_conflict,
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["repair_complete"])
        outcome = result["repair_options"][0]["session_outcomes"][0]
        self.assertEqual(outcome["after"]["day"], "Monday")
        self.assertEqual(outcome["after"]["start"], "08:30")
        self.assertEqual(outcome["after"]["room"], "R2")
        self.assertEqual(
            outcome["verification"]["original_affected_staff_booking_replaced"],
            1,
        )
        self.assertFalse(result["validation_handoff"]["ready_for_approval"])
        self.assertEqual(len(tools["check_room_availability"].calls), 1)

    def test_staff_disruption_excludes_reported_interval(self) -> None:
        affected = affected_session()
        frozen = {
            "Session ID": "F1",
            "Session Type": "Tutorial",
            "Day": "Tuesday",
            "Start": "10:15",
            "End": "11:45",
            "Weeks": "Weeks 1-12",
            "Room": "R9",
            "Expected Students": 20,
            "Cohort Group(s)": "G1",
            "Instructor": "D1",
        }
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("lecturer_or_ta_unavailable"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["S1"],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00"},
                    {"day": "Tuesday", "start": "08:30", "end": "10:00"},
                ],
            },
            [affected, frozen],
        )
        self.assertEqual(result["status"], "success")
        outcome = result["repair_options"][0]["session_outcomes"][0]
        self.assertEqual(outcome["after"]["day"], "Tuesday")
        diagnostics = result["candidate_generation"]["session_diagnostics"][0]
        self.assertEqual(
            diagnostics["rejected_candidate_counts"]["inside_disruption_scope"],
            1,
        )

    def test_unknown_staff_availability_blocks_candidate(self) -> None:
        unknown_staff = FakeTool(
            {
                "status": "information_required",
                "all_available": None,
                "summary": "Staff record is ambiguous.",
                "required_action": "Confirm the staff ID.",
            }
        )
        affected = affected_session()
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["S1"],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00"}
                ],
            },
            [affected],
            check_lecturer_or_ta_availability=unknown_staff,
        )
        self.assertEqual(result["status"], "information_required")
        self.assertFalse(result["repair_complete"])
        self.assertEqual(result["candidate_generation"]["dependency_problem_count"], 1)

    def test_day_cancellation_loads_affected_scope_and_priority_handles(self) -> None:
        affected = affected_session(day="Sunday")
        frozen = {
            "Session ID": "F1",
            "Session Type": "Tutorial",
            "Day": "Tuesday",
            "Start": "10:15",
            "End": "11:45",
            "Weeks": "Weeks 1-12",
            "Room": "R9",
            "Expected Students": 20,
            "Cohort Group(s)": "G1",
            "Instructor": "D1",
        }
        find_tool = FakeTool(
            {
                "status": "success",
                "complete": True,
                "affected_session_count": 1,
                "affected_sessions": [affected],
                "result_pagination": {"has_more": False, "next_result_offset": None},
            }
        )
        priority_tool = FakeTool(
            {
                "status": "success",
                "ranking_complete": True,
                "global_repair_order": ["S1"],
                "policy": {"confirmed": True},
            }
        )
        result, tools = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("day_cancelled", day="Sunday"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "candidate_slots": [
                    {"day": "Tuesday", "start": "08:30", "end": "10:00"}
                ],
            },
            [affected, frozen],
            find_affected_sessions=find_tool,
            check_priority=priority_tool,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(tools["find_affected_sessions"].calls), 1)
        self.assertEqual(len(tools["check_priority"].calls), 1)
        self.assertEqual(result["retrieval"]["affected_sessions"]["complete"], True)

    def test_full_567_session_day_is_all_analyzed_and_paginated(self) -> None:
        sessions = [
            affected_session(
                key=f"S{index:03d}",
                group=f"G{index:03d}",
                staff=f"D{index:03d}",
            )
            for index in range(567)
        ]
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": sessions,
                "priority_order": [item["session_id"] for item in sessions],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00"}
                ],
                "allow_day_off": True,
                "result_limit": 100,
            },
            sessions,
        )
        self.assertEqual(result["affected_session_count"], 567)
        self.assertTrue(result["analysis_complete_for_all_affected_sessions"])
        self.assertEqual(
            len(result["candidate_generation"]["session_diagnostics"]),
            100,
        )
        self.assertTrue(result["result_pagination"]["has_more"])
        self.assertEqual(result["result_pagination"]["next_result_offset"], 100)

    def test_priority_order_must_match_targeted_scope(self) -> None:
        affected = affected_session()
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["DIFFERENT"],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00"}
                ],
            },
            [affected],
        )
        self.assertEqual(result["status"], "information_required")
        self.assertFalse(result["repair_complete"])

    def test_unconfirmed_nonstandard_period_is_rejected(self) -> None:
        affected = affected_session()
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["S1"],
                "candidate_slots": [
                    {"day": "Monday", "start": "09:00", "end": "10:30"}
                ],
            },
            [affected],
        )
        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("confirmed_nonstandard", result["summary"])

    def test_unreadable_frozen_academic_group_scope_blocks_repair(self) -> None:
        affected = affected_session()
        incomplete_frozen = {
            "Session ID": "F1",
            "Session Type": "Lecture",
            "Day": "Tuesday",
            "Start": "10:15",
            "End": "11:45",
            "Weeks": "Weeks 1-12",
            "Room": "R9",
            "Expected Students": 100,
            "Instructor": "D9",
        }
        result, _ = self.invoke_with_tools(
            {
                "disruption_report": normalized_report("room_closed"),
                "general_schedule_file": "general.xlsx",
                "staff_schedule_file": "staff.xlsx",
                "room_schedule_file": "rooms.xlsx",
                "affected_sessions": [affected],
                "priority_order": ["S1"],
                "candidate_slots": [
                    {"day": "Monday", "start": "08:30", "end": "10:00"}
                ],
            },
            [affected, incomplete_frozen],
        )
        self.assertEqual(result["status"], "information_required")
        self.assertEqual(result["unreadable_frozen_row_count"], 1)


if __name__ == "__main__":
    unittest.main()
