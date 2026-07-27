# Tool catalog

The JSON schemas used by the model live in `src/self_healing_scheduler/tools/definitions.py`. This document gives the short human-facing purpose of each tool.

| Tool | Type | Brief |
|---|---|---|
| `load_schedule` | Read | Load authoritative sessions and allocations for a bounded date, department, cohort, or resource scope. |
| `load_constraints` | Read | Load hard and soft academic policies plus session-specific requirements. |
| `get_resource_availability` | Read | Check availability and suitability of lecturers, cohorts, rooms, equipment, and support resources. |
| `find_affected_scope` | Analyse | Identify directly disrupted sessions and the smallest dependency neighbourhood that may need repair. |
| `generate_repair_candidates` | Analyse | Ask the optimization service for ranked schedule patches while freezing unaffected sessions. |
| `validate_repair_candidate` | Analyse | Detect clashes and hard/soft constraint violations using current authoritative data. |
| `calculate_repair_impact` | Analyse | Count changed sessions and affected people, and assess fairness, accessibility, and sustainability. |
| `record_change_decision` | Write | Record an approver's acceptance, rejection, or requested revision. |
| `apply_schedule_patch` | Write | Atomically apply an approved and freshly validated patch to the source timetable. |
| `publish_schedule_changes` | Write | Notify calendars and stakeholders after a successful timetable update. |

## Connector mapping ideas

- University timetable/room-booking API: schedule, constraints, availability, apply patch.
- Student information system: enrolments, cohorts, accessibility requirements.
- Microsoft or Google Calendar: personal availability and post-approval publication.
- Optimization service: affected-scope analysis, candidate generation, validation, impact calculation.
- Workflow/approval system: departmental decisions and audit trail.

Tool results are untrusted data. They may supply facts, but they must never override the system prompt or authorize write operations.

