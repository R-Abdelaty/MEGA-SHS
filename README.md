# MEGA-SHS

MEGA-SHS is a React/Vite and FastAPI application for previewing the result of
university schedule cancellations. A user selects events or a date to cancel,
the existing LangChain scheduling stack proposes any required `move_time` or
`move_date` actions, and the user approves or rejects the proposal as one unit.
A movement may also select a different validated room when that room change is
needed to make the proposed time or date work; `change_room` is never a
standalone action.

The original Excel workbooks are always read-only. Approval updates an
in-memory calendar preview; it never saves over a source workbook.

The canonical HTTP contract, including request and response examples, is in
[API.md](API.md). FastAPI publishes the same schemas at `/docs` and
`/openapi.json`.

## Architecture

- `api.py` mounts the validated FastAPI router and keeps the existing
  `/api/v1` prototype routes for backward compatibility.
- `mega_shs/api_models.py` is the public Pydantic contract.
- `mega_shs/schedule.py` normalizes the recurring general and exam workbooks,
  creates date-specific stable event IDs, and hashes the authoritative source
  workbooks.
- `mega_shs/agent_adapter.py` creates a dedicated LangChain agent with
  `ToolStrategy(AgentHealingResult)`. It exposes only structured proposals.
- `mega_shs/proposal_validation.py` resolves every proposal against the current
  schedule, checks previous positions, action types, duplicates,
  contradictions, cancellation relevance, duration, authoritative room
  inventory membership, room conflicts, and student-group conflicts.
- `mega_shs/service.py` owns the asynchronous lifecycle, stale-run protection,
  approval/rejection behavior, preview generation, and history generation.
- `mega_shs/repository.py` is an async-safe, workspace-scoped, bounded
  in-memory repository. All runs, history, and previews disappear on restart.
  A future PostgreSQL adapter should implement `HealingRunRepository` here.
- `mega_shs/exporter.py` defines the future separate-file exporter boundary.
  The current placeholder is deliberately unavailable.
- `frontend/src/services/scheduleApi.js` is the only frontend HTTP client.
- `frontend/src/hooks/useHealingRun.js` owns duplicate-submit prevention,
  polling, timeout, and abort cleanup.

## Schedule normalization

The source workbooks describe academic weeks rather than calendar dates.
`SCHEDULE_SEMESTER_START_DATE` maps week 1 to a Sunday; the default for the
included synthetic dataset is `2026-07-05`.

The general workbook's source session ID is stable but recurs in weeks 1–12.
MEGA-SHS therefore hashes the canonical source filename, sheet, row, source
ID, event identity, absolute date, time, and room into IDs such as
`event_a1b2c3d4...`. Labs remain distinct source events but use the existing
tutorial/support-session visual category so the current four calendar count
categories remain unchanged.

The source schedule version is a SHA-256 hash over the canonical names and
exact bytes of authoritative `01_` through `07_` workbooks. Approval reloads
and rehashes the sources, re-resolves events, and revalidates every action.
Approved previews receive a separate `preview:sha256:...` version.

## Healing lifecycle

Normal:

```text
processing -> approval_required -> approved | rejected
```

Failure and concurrency protection:

```text
processing -> failed
processing | approval_required -> stale
```

`POST /api/healing-runs` returns `202` before the agent completes. React polls
every 1.5 seconds, stops on a terminal status, aborts on unmount, and times out
after two minutes. Approval refetches both `/api/schedule` and
`/api/change-history`. Rejection leaves the preview and history untouched.

The repository is isolated with the required `X-Workspace-ID` header. The
frontend defaults to `mega-shs-local`; deployments should assign a stable
authenticated workspace identifier. There is no user/session system in the
original repository, so this header is the minimal isolation boundary.

## Run locally

Use Python 3.12+ and Node.js/npm:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal:

```powershell
Set-Location frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Set the same `SCHEDULER_UI_API_KEY` in the backend `.env` and
`VITE_SCHEDULER_UI_API_KEY` in `frontend/.env.local`. Configure the backend-only
iHQ LiteLLM connection with `LITELLM_API_KEY`, optionally overriding
`LITELLM_API_BASE` and `LITELLM_MODEL`. The defaults are
`https://litellm.i-hq.tech/v1` and `anthropic/claude-haiku-4-5`.

`MEGA_SHS_AGENT_MODE=deterministic` is an explicit local QA mode that returns a
valid no-movement proposal without a model call. Production defaults to the
LangChain `ChatOpenAI` adapter pointed at the OpenAI-compatible iHQ LiteLLM
gateway.

## Tests and builds

Backend:

```powershell
python -m unittest discover -s tests -v
```

Frontend (the repository has no frontend test runner):

```powershell
Set-Location frontend
npm run lint
npm run build
```

The focused backend coverage includes cancellation validation, stable IDs and
hashes, lifecycle transitions, invalid structured output, unsupported,
duplicate, and contradictory actions, approval, rejection, stale and resolved
runs, missing runs, preview generation, retained cancelled events, history
sources and grouping, deterministic display formatting, unchanged source
bytes, route error contracts, workspace isolation, and export unavailability.

## Export status

`POST /api/schedule/export` returns HTTP `501` with
`{"status":"not_implemented"}`. It does not create a file and no active
download button is exposed. Implement a separate-output XLSX writer in
`mega_shs/exporter.py`; it should consume the latest approved preview and must
never write to an authoritative source path.
