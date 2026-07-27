# Architecture

## Scope

The agent coordinates a repair workflow. It should reason about the disruption, ask backend tools for facts and candidates, compare validated options, and explain the selected repair. It should not pretend to be the timetable database or the optimization solver.

## Main components

1. **Agent runtime** — sends the system prompt, conversation, and tool definitions to a model; dispatches requested tool calls; returns the final response.
2. **Schedule connectors** — read the authoritative timetable, availability, rooms, equipment, enrolments, and policies.
3. **Impact analyser** — finds directly affected sessions and the smallest dependency boundary worth reconsidering.
4. **Repair engine** — generates candidate schedule patches using constraint programming, mixed-integer optimization, local search, or another chosen approach.
5. **Validator** — checks every candidate against current authoritative data and returns hard violations, soft violations, and warnings.
6. **Approval and publishing layer** — records departmental decisions and applies an approved, validated patch to timetable/calendar systems.
7. **Reporting layer** — produces a machine-readable and human-readable impact report.

## Repair workflow

```text
Disruption received
  -> load current schedule and constraints
  -> identify affected scope
  -> generate local repair candidates
  -> validate and score candidates
  -> explain the recommended patch
  -> obtain approval
  -> revalidate against fresh data
  -> apply and publish
  -> produce impact report
```

## Optimization priorities

Use a lexicographic objective unless university policy specifies different weights:

1. Satisfy all hard constraints.
2. Preserve sessions outside the affected scope.
3. Minimize changed sessions.
4. Minimize affected students and lecturers.
5. Minimize time displacement and operational cost.
6. Improve fairness, accessibility, and sustainability.

A lower-priority gain must never justify a hard-constraint violation.

## Safety boundary

Read and analysis tools may run during planning. `apply_schedule_patch` and `publish_schedule_changes` are write operations and must only run after explicit approval. Immediately before applying a patch, the implementation should reload relevant source data and revalidate the candidate to protect against stale information.

## Suggested implementation order

1. Build an in-memory timetable connector and deterministic test dataset.
2. Implement affected-scope detection.
3. Implement hard-constraint validation.
4. Add a simple local repair solver.
5. Add impact scoring and reports.
6. Add approval and rejection handling.
7. Connect real timetable, room-booking, and calendar systems.

