"""Run a real read-only cancel-day prototype from the terminal."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.cancel_day import cancel_day


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one in-memory compensation timetable for a confirmed "
            "cancelled university day. No source workbook is modified."
        )
    )
    parser.add_argument("--day", default="Sunday", help="Cancelled weekday.")
    parser.add_argument("--week", type=int, default=1, help="Cancelled academic week.")
    parser.add_argument(
        "--reason",
        default="Confirmed full-campus closure console test.",
        help="Confirmed cancellation reason.",
    )
    parser.add_argument(
        "--following-weeks",
        type=int,
        choices=(1, 2),
        default=2,
        help="Maximum following teaching weeks used for compensation.",
    )
    parser.add_argument("--offset", type=int, default=0, help="Prototype result offset.")
    parser.add_argument(
        "--page-size",
        type=int,
        choices=range(1, 101),
        metavar="1-100",
        default=20,
        help="Number of prototype sessions printed.",
    )
    parser.add_argument("--display-week", type=int, default=None)
    parser.add_argument("--display-day", default=None)
    parser.add_argument("--display-period", default=None)
    parser.add_argument("--display-offset", type=int, default=0)
    parser.add_argument("--display-limit", type=int, default=100)
    parser.add_argument(
        "--general-file", default="05_General_Schedule.xlsx"
    )
    parser.add_argument(
        "--staff-file", default="07_Doctor_Schedule_Calendar.xlsx"
    )
    parser.add_argument("--room-file", default="01_Room_Schedule.xlsx")
    parser.add_argument("--exam-file", default="06_Exam_Schedule.xlsx")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    print(
        f"Running read-only cancellation prototype for {arguments.day}, "
        f"academic week {arguments.week}...",
        flush=True,
    )
    started = time.perf_counter()
    result = json.loads(
        cancel_day.invoke(
            {
                "day": arguments.day,
                "academic_week": arguments.week,
                "reason": arguments.reason,
                "cancellation_approved": True,
                "general_schedule_file": arguments.general_file,
                "staff_schedule_file": arguments.staff_file,
                "room_schedule_file": arguments.room_file,
                "exam_schedule_file": arguments.exam_file,
                "maximum_following_weeks": arguments.following_weeks,
                "result_offset": arguments.offset,
                "result_limit": arguments.page_size,
                "display_academic_week": arguments.display_week,
                "display_day": arguments.display_day,
                "display_period_id": arguments.display_period,
                "display_offset": arguments.display_offset,
                "display_limit": arguments.display_limit,
            }
        )
    )
    result["console_test_elapsed_seconds"] = round(
        time.perf_counter() - started, 2
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("source_files_modified") is not False:
        print("SAFETY FAILURE: the result did not confirm source immutability.", file=sys.stderr)
        return 3
    return 0 if result.get("prototype_timetable") is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
