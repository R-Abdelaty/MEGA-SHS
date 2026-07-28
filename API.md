# Scheduler UI FastAPI

The backend is a FastAPI application with typed Pydantic request bodies,
validated path/query parameters, and an OpenAPI API-key security definition.

Run the API from the repository root:

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Development documentation is available while the server is running:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

The documentation pages and schema are public so development tools can load
them. Every `/api/v1` operation remains protected by the documented
`X-API-Key` security scheme.

Copy `.env.example` to `.env` and set both backend keys. The browser sends only
`SCHEDULER_UI_API_KEY` through the `X-API-Key` header. Never expose
`ANTHROPIC_API_KEY` in frontend code, JavaScript bundles, local storage, or API
responses.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Backend and key-configuration status |
| GET | `/api/v1/options` | Static numbered dropdown definitions |
| POST | `/api/v1/intake/start` | Start the UI wizard; returns only the Day question |
| GET | `/api/v1/intake/{id}` | Read the current wizard question and answers |
| POST | `/api/v1/intake/{id}/answer` | Submit one numeric answer and receive the next question |
| GET | `/api/v1/catalogs/{name}` | Numbered dynamic resource options |
| POST | `/api/v1/disruptions/report` | Normalize any supported disruption form |
| POST | `/api/v1/prototypes/cancel-day` | Generate a read-only full-day prototype |
| GET | `/api/v1/prototypes/{id}/weeks/{week}/days/{day}` | Read a cached day or period view |
| POST | `/api/v1/agent/messages` | Send a message to the scheduler agent |

Valid catalog names are `staff`, `rooms`, `equipment`, `sessions`, and
`student-groups`. Catalogs accept `query`, `offset`, and `limit`. The sessions
catalog also accepts `day_option=1..5`.

Every `/api/v1` request requires:

```http
X-API-Key: value-of-SCHEDULER_UI_API_KEY
Content-Type: application/json
```

## Required UI wizard flow

The UI should start every request with:

```http
POST /api/v1/intake/start
```

There is no request body. The response contains one question only:

```json
{
  "status": "collecting",
  "intake_id": "INTAKE-...",
  "answers": {},
  "question": {
    "key": "day_option",
    "prompt": "Select the affected day.",
    "input_type": "single_select",
    "options": [
      {"option": 1, "label": "Sunday", "value": "Sunday"},
      {"option": 2, "label": "Monday", "value": "Monday"},
      {"option": 3, "label": "Tuesday", "value": "Tuesday"},
      {"option": 4, "label": "Wednesday", "value": "Wednesday"},
      {"option": 5, "label": "Thursday", "value": "Thursday"}
    ]
  },
  "ready_to_execute": false
}
```

Send exactly one answer to receive the next dropdown:

```http
POST /api/v1/intake/INTAKE-.../answer
```

```json
{"option": 2}
```

The fixed first three questions are **Day**, **Academic week**, then **Problem**.
After Problem, the middleware returns only the details relevant to that problem.
For a multi-select catalog use `{"options": [1, 3]}`; for a positive number use
`{"number": 80}`; for required free text use `{"text": "explanation"}`.

No disruption, repair, or scheduling tool runs while `status` is `collecting`.
The selected workflow runs exactly once only after the final confirmation answer
is option `1`. Confirmation option `2` cancels the intake and runs nothing.

## Full-day prototype request

```json
{
  "day_option": 2,
  "academic_week": 1,
  "confirmation_option": 1,
  "maximum_following_weeks": 2,
  "result_offset": 0,
  "result_limit": 50
}
```

## Day and period view

Monday is day option `2`; P1 is period option `1`:

```http
GET /api/v1/prototypes/PRT-123/weeks/1/days/2?period_option=1&offset=0&limit=100
```

The response contains `prototype_timetable.selected_day_schedule.slot_groups`,
color tokens, counts, sessions, and pagination metadata.

## Generic disruption request

Doctor option numbers come from `/api/v1/catalogs/staff`:

```json
{
  "problem_option": 1,
  "day_option": 2,
  "academic_week": 1,
  "resource_options": [1],
  "scope_option": 1,
  "start_period_option": 2,
  "confirmation_option": 1
}
```

The API resolves every numeric selection before invoking `report_disruption`.
Source workbooks remain read-only.
