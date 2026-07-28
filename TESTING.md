# Scheduler tool tests

Run the complete suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

Run the single comprehensive console check. This runs the complete automated
suite, creates a real Monday/week-1 cancellation prototype from the fake data,
validates the candidate, and confirms that all seven source workbooks remain
byte-for-byte unchanged. It also independently checks every compensation row
and fails if any attached student group is placed on its normal day off:

```powershell
& "C:\Users\tarek sherif\AppData\Local\Programs\Python\Python314\python.exe" .\tests\run_everything_console.py
```

For a quick run that skips the several-minute live cancellation scenario:

```powershell
& "C:\Users\tarek sherif\AppData\Local\Programs\Python\Python314\python.exe" .\tests\run_everything_console.py --skip-live
```

Run the UI API locally after configuring `.env`:

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

The suite covers every tool that currently has a real implementation:

- `get_schedule`
- `report_disruption`
- `cancel_day`
- `find_affected_sessions`
- `check_priority`
- `check_lecturer_or_ta_availability`
- `check_room_availability`
- `run_schedule_repair`
- `compare_schedule_versions`
- `check_validity`

It tests input validation, safe failure behavior, schedule filtering and
pagination, disruption normalization, affected-session scope, strict repair
priority, staff conflicts, room constraints, side-effect-free repair output,
version comparison, and final conflict detection.

`approve_repair` is excluded because it still explicitly returns a `TODO`
placeholder. It should receive contract tests when implemented.

## Real cancel-day console test

Run the complete Sunday/week-1 prototype against the fake-data workbooks:

```powershell
python .\tests\run_cancel_day_console.py --day Sunday --week 1 --following-weeks 2 --page-size 20
```

Select one combined UI day view (normal and compensation sessions together):

```powershell
python .\tests\run_cancel_day_console.py --day Sunday --week 1 --display-week 1 --display-day Monday --display-period P1 --display-limit 100
```

The first calculation can take several minutes because it processes the full
cancelled-day scope. Compensation starts on the next teaching day, using the
remaining days of the cancelled week before the following one or two weeks. It
prints a paginated prototype and never writes to the source workbooks.

The cancellation response also includes `extreme_case.alerts`. These alerts are
inactive in normal cases. Near a midterm, major exam, or final, an unassigned
session can raise explicit-authorization alerts for day-off attendance and an
additional official compensation day. The test suite verifies that neither
exception is scheduled or applied automatically.
