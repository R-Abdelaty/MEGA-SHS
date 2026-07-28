# Scheduler tool tests

Run the complete suite from the repository root:

```powershell
python -m unittest discover -s tests -v
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

The first calculation can take several minutes because it processes the full
cancelled-day scope. It prints a paginated prototype and never writes to the
source workbooks.
