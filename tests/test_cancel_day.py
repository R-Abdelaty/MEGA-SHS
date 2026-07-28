"""End-to-end contract tests for the read-only cancellation orchestrator."""

from __future__ import annotations

import importlib
import json
import unittest
from unittest.mock import patch


cancel_module = importlib.import_module("tools.cancel_day")
cancel_day = cancel_module.cancel_day


class FakeTool:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def invoke(self, arguments: dict):
        self.calls.append(arguments)
        result = self.responder(arguments) if callable(self.responder) else self.responder
        return result if isinstance(result, str) else json.dumps(result)


def extraction_payload(rows: list[dict], sheet: str) -> dict:
    return {
        "status": "ok",
        "extraction": {
            "matching_rows_found": len(rows),
            "matching_rows_returned": len(rows),
            "has_more": False,
            "next_row_offset": None,
            "sheets": [
                {
                    "name": sheet,
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
        "limits": {"truncated": False, "completeness": "complete"},
    }


def session(
    key: str,
    session_type: str,
    period: str,
    start: str,
    end: str,
    group: str,
    staff: str,
    room_type: str,
) -> dict:
    return {
        "affected_session_key": key,
        "session_id": key,
        "course_id": f"C-{key}",
        "course_name": f"Course {key}",
        "session_type": session_type,
        "day": "Sunday",
        "period": period,
        "start": start,
        "end": end,
        "week": "Weeks 1-12",
        "room": "R1",
        "room_type": room_type,
        "expected_students": 25,
        "student_groups": group,
        "instructor": staff,
        "major": "CSE",
        "year": 1,
        "status": "Active",
    }


class CancelDayTests(unittest.TestCase):
    def setUp(self) -> None:
        cancel_module._PROTOTYPE_CACHE.clear()
        self.lecture = session(
            "S1", "Lecture", "P1", "08:30", "10:00", "G1", "Dr. One", "Lecture Hall"
        )
        self.tutorial = session(
            "S2", "Tutorial", "P2", "10:15", "11:45", "G2", "TA One", "Tutorial Room"
        )
        self.normal_monday = session(
            "N1", "Tutorial", "P3", "13:00", "14:30", "G3", "TA Two", "Tutorial Room"
        )
        self.normal_monday["day"] = "Monday"
        self.report_tool = FakeTool(
            {
                "status": "success",
                "report_complete": True,
                "disruption_report": {
                    "disruption_id": "DSP-CANCELTEST",
                    "disruption_type": "day_cancelled",
                    "scope": {
                        "affected_day": "Sunday",
                        "affected_date": None,
                        "academic_week": 1,
                        "whole_day": True,
                        "start_time": None,
                        "end_time": None,
                    },
                },
            }
        )
        self.find_tool = FakeTool(
            {
                "status": "success",
                "complete": True,
                "affected_session_count": 2,
                "affected_sessions": [self.lecture, self.tutorial],
                "result_pagination": {"has_more": False, "next_result_offset": None},
            }
        )
        self.priority_tool = FakeTool(
            {
                "status": "success",
                "ranking_complete": True,
                "global_repair_order": ["S1", "S2"],
            }
        )

        def schedule_response(arguments: dict) -> dict:
            sheet = arguments["sheet_name"]
            if sheet == "Semester Timetable":
                return extraction_payload(
                    [self.lecture, self.tutorial, self.normal_monday], sheet
                )
            if sheet == "Regular Assessments":
                return extraction_payload([], sheet)
            if sheet == "Doctor Directory":
                return extraction_payload([{"Doctor ID": "D1", "Doctor Name": "Dr. One"}], sheet)
            raise AssertionError(f"Unexpected sheet: {sheet}")

        self.schedule_tool = FakeTool(schedule_response)
        self.staff_tool = FakeTool(
            {
                "status": "success",
                "all_available": True,
                "available_staff_ids": ["Dr. One", "TA One"],
                "unavailable_staff_ids": [],
                "unknown_staff_ids": [],
                "staff_results": [],
            }
        )

        def room_response(arguments: dict) -> dict:
            return {
                "status": "success",
                "available_room_count": 2,
                "available_rooms": [
                    {"room": "LH1", "type": "Lecture Hall", "capacity": 100},
                    {"room": "T1", "type": "Tutorial Room", "capacity": 30},
                ],
            }

        self.room_tool = FakeTool(room_response)

    def invoke(self, **overrides) -> dict:
        arguments = {
            "day": "Sunday",
            "academic_week": 1,
            "reason": "Confirmed campus closure.",
            "cancellation_approved": True,
            "maximum_following_weeks": 2,
            "result_limit": 1,
        }
        arguments.update(overrides)
        with (
            patch.object(cancel_module, "report_disruption", self.report_tool),
            patch.object(cancel_module, "find_affected_sessions", self.find_tool),
            patch.object(cancel_module, "check_priority", self.priority_tool),
            patch.object(cancel_module, "get_schedule", self.schedule_tool),
            patch.object(
                cancel_module,
                "check_lecturer_or_ta_availability",
                self.staff_tool,
            ),
            patch.object(cancel_module, "check_room_availability", self.room_tool),
        ):
            return json.loads(cancel_day.invoke(arguments))

    def test_returns_one_paginated_prototype_starting_next_teaching_day(self) -> None:
        result = self.invoke()

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["prototype_complete"])
        self.assertFalse(result["source_files_modified"])
        self.assertEqual(result["unassigned_session_count"], 0)
        timetable = result["prototype_timetable"]
        self.assertEqual(timetable["status"], "pending_user_confirmation")
        self.assertEqual(timetable["target_academic_weeks"], [1, 2, 3])
        self.assertTrue(timetable["compensation_starts_after_cancelled_day"])
        self.assertEqual(timetable["total_compensation_sessions"], 2)
        self.assertEqual(timetable["returned_compensation_sessions"], 1)
        self.assertTrue(timetable["pagination"]["has_more"])
        self.assertEqual(timetable["sessions"][0]["academic_week"], 1)
        self.assertEqual(timetable["sessions"][0]["day"], "Monday")
        monday_view = timetable["selected_day_schedule"]
        self.assertEqual((monday_view["academic_week"], monday_view["day"]), (1, "Monday"))
        self.assertEqual(monday_view["normal_session_count"], 1)
        self.assertEqual(monday_view["compensation_session_count"], 2)
        self.assertEqual(
            {item["schedule_status"] for item in monday_view["sessions"]},
            {"normal", "compensation"},
        )
        self.assertEqual(
            {item["display"]["color_name"] for item in monday_view["sessions"]},
            {"gray", "green"},
        )
        slots = {item["period_id"]: item for item in monday_view["slot_groups"]}
        self.assertEqual(slots["P1"]["compensation_session_count"], 2)
        self.assertEqual(slots["P2"]["compensation_session_count"], 0)
        self.assertEqual(slots["P3"]["normal_session_count"], 1)
        self.assertEqual(slots["P1"]["display"]["color_name"], "green")
        self.assertEqual(timetable["color_legend"]["cancelled"]["color_name"], "red")
        monday_summary = next(
            item
            for item in timetable["day_views"]
            if item["academic_week"] == 1 and item["day"] == "Monday"
        )
        self.assertTrue(monday_summary["has_compensation"])
        self.assertEqual(monday_summary["total_session_count"], 3)
        self.assertTrue(
            result["constraint_summary"][
                "remaining_cancelled_week_days_considered_first"
            ]
        )
        self.assertFalse(
            result["constraint_summary"][
                "same_or_earlier_cancelled_week_days_allowed"
            ]
        )
        self.assertEqual(result["approval"]["status"], "not_requested")

    def test_requires_explicit_cancellation_confirmation(self) -> None:
        result = self.invoke(cancellation_approved=False)

        self.assertEqual(result["status"], "invalid_request")
        self.assertFalse(result["prototype_complete"])
        self.assertEqual(self.report_tool.calls, [])

    def test_following_pages_reuse_the_computed_prototype(self) -> None:
        first = self.invoke(result_offset=0, result_limit=1)
        report_call_count = len(self.report_tool.calls)
        second = self.invoke(result_offset=1, result_limit=1)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(len(self.report_tool.calls), report_call_count)
        self.assertTrue(second["orchestration"]["cache_hit"])
        self.assertEqual(
            {
                first["prototype_timetable"]["sessions"][0]["session_id"],
                second["prototype_timetable"]["sessions"][0]["session_id"],
            },
            {"S1", "S2"},
        )

    def test_ui_can_select_and_paginate_a_combined_day_view(self) -> None:
        first = self.invoke(
            display_academic_week=1,
            display_day="Monday",
            display_offset=0,
            display_limit=1,
        )
        report_call_count = len(self.report_tool.calls)
        second = self.invoke(
            display_academic_week=1,
            display_day="Monday",
            display_offset=1,
            display_limit=2,
        )

        first_view = first["prototype_timetable"]["selected_day_schedule"]
        second_view = second["prototype_timetable"]["selected_day_schedule"]
        self.assertEqual(first_view["returned_session_count"], 1)
        self.assertTrue(first_view["pagination"]["has_more"])
        self.assertEqual(first_view["pagination"]["next_display_offset"], 1)
        self.assertEqual(second_view["returned_session_count"], 2)
        self.assertFalse(second_view["pagination"]["has_more"])
        self.assertEqual(len(self.report_tool.calls), report_call_count)
        self.assertTrue(second["orchestration"]["cache_hit"])

        period = self.invoke(
            display_academic_week=1,
            display_day="Monday",
            display_period_id="P1",
            display_limit=100,
        )
        period_view = period["prototype_timetable"]["selected_day_schedule"]
        self.assertEqual(period_view["selected_period_id"], "P1")
        self.assertEqual(period_view["filtered_session_count"], 2)
        self.assertEqual(
            {item["schedule_status"] for item in period_view["sessions"]},
            {"compensation"},
        )
        self.assertEqual(len(self.report_tool.calls), report_call_count)

    def test_rejects_day_view_outside_compensation_window(self) -> None:
        result = self.invoke(display_academic_week=1, display_day="Sunday")

        self.assertEqual(result["status"], "invalid_request")
        self.assertEqual(self.report_tool.calls, [])

    def test_rejects_unknown_display_period(self) -> None:
        result = self.invoke(
            display_academic_week=1,
            display_day="Monday",
            display_period_id="P99",
        )

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("display_period_id", result["summary"])

    def test_week_twelve_thursday_has_no_later_teaching_day(self) -> None:
        result = self.invoke(day="Thursday", academic_week=12)

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("finals blackout", result["summary"])

    def test_reports_progress_without_changing_the_result(self) -> None:
        events: list[tuple[str, int | None, int | None]] = []
        cancel_module.set_cancel_day_progress_reporter(
            lambda phase, completed, total: events.append(
                (phase, completed, total)
            )
        )
        try:
            result = self.invoke(result_limit=2)
        finally:
            cancel_module.set_cancel_day_progress_reporter(None)

        self.assertEqual(result["status"], "success")
        self.assertIn(
            ("Checking rooms and candidate slots", 2, 2),
            events,
        )
        self.assertEqual(events[-1], ("Prototype ready", 1, 1))


if __name__ == "__main__":
    unittest.main()
