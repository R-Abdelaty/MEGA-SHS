"""Tests for concise terminal result output."""

from __future__ import annotations

import unittest

from console_presenter import format_console_result


class ConsolePresenterTests(unittest.TestCase):
    def test_prototype_summary_omits_session_rows(self) -> None:
        result = {
            "status": "success",
            "summary": "One read-only prototype assigned 528 of 528 sessions.",
            "prototype_id": "PRT-TEST",
            "cancelled_scope": {
                "day": "Monday",
                "academic_week": 1,
                "affected_session_count": 528,
            },
            "prototype_timetable": {
                "total_compensation_sessions": 528,
                "sessions": [
                    {
                        "session_id": "LEC-SECRET-LONG-ROW",
                        "room": "X-LH-01",
                    }
                ],
                "day_views": [
                    {
                        "academic_week": 1,
                        "day": "Tuesday",
                        "normal_session_count": 400,
                        "compensation_session_count": 120,
                        "has_compensation": True,
                    },
                    {
                        "academic_week": 1,
                        "day": "Wednesday",
                        "normal_session_count": 410,
                        "compensation_session_count": 0,
                        "has_compensation": False,
                    },
                ],
            },
            "unassigned_session_count": 0,
            "source_files_modified": False,
            "required_action": "Review the prototype.",
        }

        output = format_console_result(result)

        self.assertIn("Prototype ID: PRT-TEST", output)
        self.assertIn("Affected sessions: 528", output)
        self.assertIn("Week 1, Tuesday: 120 new compensation, 400 normal", output)
        self.assertNotIn("Week 1, Wednesday", output)
        self.assertNotIn("LEC-SECRET-LONG-ROW", output)
        self.assertNotIn("X-LH-01", output)
        self.assertIn("Source schedule modified: No", output)

    def test_disruption_report_is_also_compact(self) -> None:
        output = format_console_result(
            {
                "status": "success",
                "summary": "Disruption recorded.",
                "disruption_report": {"disruption_id": "DSP-123", "large": [1, 2, 3]},
            }
        )

        self.assertIn("Disruption ID: DSP-123", output)
        self.assertNotIn("large", output)


if __name__ == "__main__":
    unittest.main()
