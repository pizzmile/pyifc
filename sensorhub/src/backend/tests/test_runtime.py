import asyncio
import json
import unittest
from urllib.parse import urlsplit

from backend.src.adapters import InMemoryObjectStore, InMemoryQueues
from backend.src.processing import process_line_voltage_job, process_speed_job
from backend.src.runtime import create_app


def request_app(app, method, target, body=b""):
    parsed_target = urlsplit(target)
    sent_request = False
    messages = []

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    async def invoke():
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": parsed_target.path,
                "raw_path": parsed_target.path.encode("ascii"),
                "query_string": parsed_target.query.encode("ascii"),
                "root_path": "",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 12345),
                "server": ("test", 80),
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = next(message for message in messages if message["type"] == "http.response.body")
    body = response_body.get("body", b"")
    content_type = dict(response_start.get("headers", [])).get(b"content-type", b"")
    if b"application/json" in content_type:
        body = json.loads(body or b"{}")
    else:
        body = body.decode("utf-8")
    return response_start["status"], body


class RuntimeTests(unittest.TestCase):
    def test_health_endpoint_reports_ready(self):
        app = create_app(
            InMemoryObjectStore(), InMemoryQueues(), InMemoryObjectStore()
        )

        status_code, response_body = request_app(app, "GET", "/healthz")

        self.assertEqual(status_code, 200)
        self.assertEqual(response_body, {"status": "ok"})

    def test_http_api_round_trip_through_kind_specific_workers(self):
        staging = InMemoryObjectStore()
        queues = InMemoryQueues()
        results = InMemoryObjectStore()
        app = create_app(staging, queues, results)

        speed_status, _ = request_app(
            app,
            "POST",
            "/readings",
            json.dumps({
                "kind": "speed",
                "fleet_id": "demo-fleet",
                "asset_id": "train-01",
                "timestamp": "2026-09-23T10:00:00Z",
                "value": 132.6,
            }).encode("utf-8"),
        )
        voltage_status, _ = request_app(
            app,
            "POST",
            "/readings",
            json.dumps({
                "kind": "line_voltage",
                "fleet_id": "demo-fleet",
                "asset_id": "train-01",
                "timestamp": "2026-09-23T10:00:01Z",
                "value": 26680,
            }).encode("utf-8"),
        )
        self.assertEqual(speed_status, 202)
        self.assertEqual(voltage_status, 202)

        process_speed_job(queues.consume("speed"), staging, results)
        process_line_voltage_job(queues.consume("line_voltage"), staging, results)

        status_code, response_body = request_app(app, "GET", "/readings?limit=10")
        self.assertEqual(status_code, 200)
        readings = response_body["readings"]
        self.assertEqual(
            [(reading["kind"], reading["unit"], reading["status"]) for reading in readings],
            [
                ("line_voltage", "V", "voltage_observed"),
                ("speed", "km/h", "moving"),
            ],
        )

    def test_invalid_limit_is_a_client_error(self):
        app = create_app(
            InMemoryObjectStore(), InMemoryQueues(), InMemoryObjectStore()
        )

        status_code, response_body = request_app(app, "GET", "/readings?limit=all")

        self.assertEqual(status_code, 400)
        self.assertEqual(response_body["error"]["code"], "invalid_limit")

    def test_train_filter_is_applied_before_limit(self):
        results = InMemoryObjectStore()
        for reading_index in range(105):
            reading = {
                "id": f"target-{reading_index:03}",
                "kind": "speed",
                "fleet_id": "demo-fleet",
                "asset_id": "train-01",
                "timestamp": f"2026-09-24T10:{reading_index // 60:02}:{reading_index % 60:02}Z",
                "value": reading_index,
                "unit": "km/h",
                "status": "moving",
            }
            results.put(
                reading["id"], json.dumps(reading).encode(), "application/json"
            )
        unrelated = {
            "id": "other-train",
            "kind": "speed",
            "fleet_id": "demo-fleet",
            "asset_id": "train-02",
            "timestamp": "2026-09-24T11:00:00Z",
            "value": 1,
            "unit": "km/h",
            "status": "moving",
        }
        results.put("other-train", json.dumps(unrelated).encode(), "application/json")
        app = create_app(InMemoryObjectStore(), InMemoryQueues(), results)

        status_code, response_body = request_app(
            app,
            "GET",
            "/readings?limit=100&fleet_id=demo-fleet&asset_id=train-01",
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(len(response_body["readings"]), 100)
        self.assertTrue(
            all(reading["asset_id"] == "train-01" for reading in response_body["readings"])
        )
        self.assertEqual(response_body["readings"][0]["id"], "target-104")

    def test_train_filter_requires_both_identifiers(self):
        app = create_app(
            InMemoryObjectStore(), InMemoryQueues(), InMemoryObjectStore()
        )

        status_code, response_body = request_app(
            app, "GET", "/readings?fleet_id=demo-fleet"
        )

        self.assertEqual(status_code, 400)
        self.assertEqual(response_body["error"]["code"], "invalid_train_filter")


if __name__ == "__main__":
    unittest.main()