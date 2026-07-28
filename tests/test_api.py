"""Contract tests for the UI-facing FastAPI application."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
from api import DisruptionForm, disruption_arguments
from api_contract import ui_options
from intake_middleware import clear_intakes


class FakeTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        return json.dumps(
            {
                "status": "success",
                "prototype_complete": True,
                "prototype_id": "PRT-API-TEST",
                "prototype_timetable": {
                    "selected_day_schedule": {
                        "academic_week": arguments.get("display_academic_week"),
                        "day": arguments.get("display_day"),
                        "selected_period_id": arguments.get("display_period_id"),
                    }
                },
                "source_files_modified": False,
            }
        )


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        api._PROTOTYPE_REQUESTS.clear()
        clear_intakes()

    def test_static_options_are_stable_and_numeric(self) -> None:
        options = ui_options()

        self.assertEqual(options["days"][0], {"option": 1, "label": "Sunday", "value": "Sunday"})
        self.assertEqual(options["problem_types"][0]["value"], "lecturer_or_ta_unavailable")
        self.assertEqual(options["periods"][4]["period_id"], "P5")
        self.assertEqual(options["academic_weeks"][-1]["option"], 12)
        self.assertNotIn("cancellation_reasons", options)

    def test_fastapi_exposes_interactive_docs_and_typed_openapi(self) -> None:
        self.assertIsInstance(api.app, FastAPI)
        client = TestClient(api.app)

        docs = client.get("/docs")
        redoc = client.get("/redoc")
        openapi = client.get("/openapi.json")

        self.assertEqual(docs.status_code, 200)
        self.assertEqual(redoc.status_code, 200)
        self.assertEqual(openapi.status_code, 200)
        schema = openapi.json()
        self.assertEqual(
            schema["info"]["title"],
            "Self-Healing University Scheduler API",
        )
        self.assertEqual(
            schema["components"]["securitySchemes"]["APIKeyHeader"],
            {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        )
        self.assertEqual(
            schema["paths"]["/api/v1/options"]["get"]["security"],
            [{"APIKeyHeader": []}],
        )
        request_schema = schema["paths"][
            "/api/v1/prototypes/cancel-day"
        ]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(
            request_schema["$ref"],
            "#/components/schemas/CancelDayPrototypeForm",
        )

    def test_numeric_partial_day_form_maps_to_tool_arguments(self) -> None:
        arguments = disruption_arguments(
            DisruptionForm(
                problem_option=5,
                day_option=3,
                academic_week=2,
                scope_option=2,
                start_period_option=1,
                end_period_option=2,
            )
        )

        self.assertEqual(arguments["disruption_type"], "partial_day_cancelled")
        self.assertEqual(arguments["affected_day_or_date"], "Tuesday")
        self.assertEqual(arguments["start_time"], "08:30")
        self.assertEqual(arguments["end_time"], "11:45")
        self.assertFalse(arguments["whole_day"])

    def test_api_key_is_required_for_v1_endpoints(self) -> None:
        client = TestClient(api.app)
        with patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}):
            unauthorized = client.get("/api/v1/options")
            authorized = client.get(
                "/api/v1/options", headers={"X-API-Key": "test-secret"}
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["api_version"], "v1")

    def test_fastapi_validation_uses_the_stable_error_envelope(self) -> None:
        client = TestClient(api.app)
        with patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}):
            response = client.post(
                "/api/v1/prototypes/cancel-day",
                headers={"X-API-Key": "test-secret"},
                json={"day_option": 9, "unexpected": True},
            )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"]["code"], "invalid_request_body")
        self.assertTrue(payload["error"]["details"])

    def test_intake_starts_with_only_the_day_question(self) -> None:
        client = TestClient(api.app)
        headers = {"X-API-Key": "test-secret"}
        fake_cancel_day = FakeTool()
        fake_report = FakeTool()
        with (
            patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}),
            patch.object(api, "cancel_day", fake_cancel_day),
            patch.object(api, "report_disruption", fake_report),
        ):
            response = client.post("/api/v1/intake/start", headers=headers)

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["status"], "collecting")
        self.assertEqual(payload["answers"], {})
        self.assertEqual(payload["question"]["key"], "day_option")
        self.assertEqual(
            [item["label"] for item in payload["question"]["options"]],
            ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
        )
        self.assertEqual(fake_cancel_day.calls, [])
        self.assertEqual(fake_report.calls, [])

    def test_full_day_intake_executes_only_after_final_confirmation(self) -> None:
        client = TestClient(api.app)
        headers = {"X-API-Key": "test-secret"}
        fake = FakeTool()
        with (
            patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}),
            patch.object(api, "cancel_day", fake),
        ):
            started = client.post("/api/v1/intake/start", headers=headers).json()
            intake_id = started["intake_id"]
            expected_questions = [
                (2, "academic_week"),
                (1, "problem_option"),
                (4, "confirmation_option"),
            ]
            for option, expected_question in expected_questions:
                response = client.post(
                    f"/api/v1/intake/{intake_id}/answer",
                    headers=headers,
                    json={"option": option},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["question"]["key"], expected_question)
                self.assertEqual(fake.calls, [])

            confirmed = client.post(
                f"/api/v1/intake/{intake_id}/answer",
                headers=headers,
                json={"option": 1},
            )

        self.assertEqual(confirmed.status_code, 200)
        payload = confirmed.json()
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["ready_to_execute"])
        self.assertEqual(payload["execution"]["prototype_id"], "PRT-API-TEST")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["day"], "Monday")
        self.assertEqual(fake.calls[0]["academic_week"], 1)
        self.assertEqual(
            fake.calls[0]["reason"],
            "Confirmed full university day cancellation.",
        )

    def test_cancelled_intake_never_executes_a_tool(self) -> None:
        client = TestClient(api.app)
        headers = {"X-API-Key": "test-secret"}
        fake = FakeTool()
        with (
            patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}),
            patch.object(api, "cancel_day", fake),
        ):
            intake_id = client.post("/api/v1/intake/start", headers=headers).json()[
                "intake_id"
            ]
            for option in (1, 1, 4):
                response = client.post(
                    f"/api/v1/intake/{intake_id}/answer",
                    headers=headers,
                    json={"option": option},
                )
                self.assertEqual(response.status_code, 200)
            cancelled = client.post(
                f"/api/v1/intake/{intake_id}/answer",
                headers=headers,
                json={"option": 2},
            )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertFalse(cancelled.json()["ready_to_execute"])
        self.assertEqual(fake.calls, [])

    def test_prototype_creation_and_slot_view_reuse_the_stored_request(self) -> None:
        client = TestClient(api.app)
        fake = FakeTool()
        headers = {"X-API-Key": "test-secret"}
        with (
            patch.dict(os.environ, {"SCHEDULER_UI_API_KEY": "test-secret"}),
            patch.object(api, "cancel_day", fake),
        ):
            created = client.post(
                "/api/v1/prototypes/cancel-day",
                headers=headers,
                json={
                    "day_option": 1,
                    "academic_week": 1,
                    "confirmation_option": 1,
                },
            )
            viewed = client.get(
                "/api/v1/prototypes/PRT-API-TEST/weeks/1/days/2",
                headers=headers,
                params={"period_option": 1, "offset": 0, "limit": 25},
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1]["display_day"], "Monday")
        self.assertEqual(fake.calls[1]["display_period_id"], "P1")
        self.assertEqual(fake.calls[1]["display_limit"], 25)


if __name__ == "__main__":
    unittest.main()
