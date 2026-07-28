"""Run every implemented automated and live fake-data scheduler check."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"
FAKE_DATA_DIR = PROJECT_ROOT / "fake data"
AUTHORITATIVE_FILES = [
    "01_Room_Schedule.xlsx",
    "02_Lab_Equipment.xlsx",
    "03_Student_Enrollment.xlsx",
    "04_Course_Catalog.xlsx",
    "05_General_Schedule.xlsx",
    "06_Exam_Schedule.xlsx",
    "07_Doctor_Schedule_Calendar.xlsx",
]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run all implemented scheduler tests plus one real, read-only "
            "cancel-day prototype and validity check."
        )
    )
    parser.add_argument("--day", default="Monday")
    parser.add_argument("--week", type=int, choices=range(1, 13), default=1)
    parser.add_argument("--following-weeks", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-issues", type=int, default=1000)
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Run only the fast automated suite and source-integrity check.",
    )
    return parser.parse_args()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_hashes() -> dict[str, str]:
    missing = [name for name in AUTHORITATIVE_FILES if not (FAKE_DATA_DIR / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing authoritative fake-data files: " + ", ".join(missing))
    return {name: _digest(FAKE_DATA_DIR / name) for name in AUTHORITATIVE_FILES}


def _run(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return completed.returncode


def main() -> int:
    arguments = _arguments()
    started = time.perf_counter()
    print("FULL SCHEDULER SYSTEM CHECK", flush=True)
    print("[1/3] Recording hashes for all seven authoritative workbooks...", flush=True)
    try:
        hashes_before = _source_hashes()
    except (FileNotFoundError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    print("[2/3] Running all automated tests...", flush=True)
    unit_code = _run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(TESTS_DIR),
            "-v",
        ]
    )

    live_code: int | None = None
    if arguments.skip_live:
        print("[3/3] Live cancel-day validity scenario skipped by request.", flush=True)
    elif unit_code == 0:
        print(
            f"[3/3] Running real {arguments.day}, week {arguments.week} "
            "cancel-day prototype and check_validity...",
            flush=True,
        )
        live_code = _run(
            [
                sys.executable,
                str(TESTS_DIR / "run_cancel_day_validity_console.py"),
                "--day",
                arguments.day,
                "--week",
                str(arguments.week),
                "--following-weeks",
                str(arguments.following_weeks),
                "--max-issues",
                str(arguments.max_issues),
            ]
        )
    else:
        print("[3/3] Live scenario skipped because automated tests failed.", flush=True)

    try:
        source_unchanged = hashes_before == _source_hashes()
    except (FileNotFoundError, OSError) as exc:
        print(f"FAIL: source-integrity recheck failed: {exc}", file=sys.stderr)
        source_unchanged = False
    live_passed = arguments.skip_live or live_code == 0
    passed = unit_code == 0 and live_passed and source_unchanged

    print("\nFINAL CHECK SUMMARY")
    print(f"- Automated tests: {'PASS' if unit_code == 0 else 'FAIL'}")
    print(
        "- Live fake-data cancellation + validity: "
        + ("SKIPPED" if arguments.skip_live else "PASS" if live_code == 0 else "FAIL")
    )
    print(f"- All seven source workbooks unchanged: {'PASS' if source_unchanged else 'FAIL'}")
    print(f"- Elapsed: {time.perf_counter() - started:.2f} seconds")
    print(f"OVERALL RESULT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
