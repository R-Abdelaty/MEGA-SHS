# Self-Healing University Scheduler

This repository contains an intentionally small, provider-neutral agent skeleton. It defines the agent's responsibilities, the tool contracts it will eventually call, and the core scheduling data structures. It does **not** yet contain a scheduling solver, university-system connector, calendar integration, or user interface.

## Project map

```text
.
├── docs/
│   ├── ARCHITECTURE.md          # Boundaries, workflow, and implementation order
│   └── TOOL_CATALOG.md          # Human-readable tool briefs
├── examples/
│   └── lecturer_unavailable.json
├── src/self_healing_scheduler/
│   ├── agent.py                 # Provider-neutral orchestration loop
│   ├── models.py                # Shared domain data structures
│   ├── prompts/system_prompt.md # Main agent system prompt
│   └── tools/
│       ├── definitions.py       # Model-facing JSON tool schemas
│       ├── handlers.py          # Placeholder backend implementations
│       └── registry.py          # Tool dispatch and validation boundary
└── tests/
    └── test_tool_definitions.py # Basic scaffold checks
```

## Design goals

- Preserve unaffected timetable sessions.
- Treat academic, safety, capacity, accessibility, and resource rules as explicit constraints.
- Repair the smallest feasible part of the schedule.
- Validate a proposal before any write operation.
- Require explicit approval before publishing changes.
- Explain every changed session and every relaxed soft constraint.

## Start here

1. Read `docs/ARCHITECTURE.md`.
2. Edit `src/self_healing_scheduler/prompts/system_prompt.md` as policies become concrete.
3. Connect real systems by replacing the `NotImplementedError` methods in `tools/handlers.py`.
4. Add an optimization engine behind `generate_repair_candidates`.
5. Implement a model adapter satisfying the `ModelClient` protocol in `agent.py`.

## Run the scaffold checks

No third-party packages are required:

```powershell
python -m unittest discover -s tests -v
```

The old `hello.txt` file is retained because it was part of the repository's initial history.

