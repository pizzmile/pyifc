import json
import unittest

from backend.src.ingestion import handle_post_readings


class RecordingStaging:
    def __init__(self, events=None, fail=False):
        self.objects = {}
        self.events = events if events is not None else []
        self.fail = fail

    def put(self, key, body, content_type):
        self.events.append(("stage", key))
        if self.fail:
            raise OSError("storage unavailable")
        self.objects[key] = (body, content_type)


class RecordingQueues:
    def __init__(self, events=None, fail=False):
        self.messages = []
        self.events = events if events is not None else []
        self.fail = fail

    def publish(self, kind, job):
        self.events.append(("publish", kind))
        if self.fail:
            raise OSError("queue unavailable")
        self.messages.append((kind, job))


class IngestionTests(unittest.TestCase):
    def test_valid_speed_reading_is_staged_then_published(self):
        body = (
            b'{"kind":"speed","fleet_id":"demo-fleet",'
            b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
            b'"value":132.6}'
        )
        reading_id = "123e4567-e89b-12d3-a456-426614174000"
        events = []
        staging = RecordingStaging(events)
        queues = RecordingQueues(events)

        response = handle_post_readings(
            body,
            "application/json; charset=utf-8",
            staging,
            queues,
            id_factory=lambda: reading_id,
        )

        staging_key = f"raw/{reading_id}.json"
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.body, {"id": reading_id})
        self.assertEqual(staging.objects[staging_key], (body, "application/json"))
        self.assertEqual(queues.messages, [("speed", {"id": reading_id, "staging_key": staging_key})])
        self.assertEqual(events, [("stage", staging_key), ("publish", "speed")])

    def test_valid_voltage_reading_uses_voltage_queue(self):
        body = (
            b'{"kind":"line_voltage","fleet_id":"demo-fleet",'
            b'"asset_id":"train-02","timestamp":"2026-09-23T10:00:00Z",'
            b'"value":26680}'
        )
        staging = RecordingStaging()
        queues = RecordingQueues()

        response = handle_post_readings(
            body,
            "application/json",
            staging,
            queues,
            id_factory=lambda: "123e4567-e89b-12d3-a456-426614174001",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(queues.messages[0][0], "line_voltage")

    def test_in_memory_smoke_flow_accepts_both_reading_kinds(self):
        readings = (
            b'{"kind":"speed","fleet_id":"demo-fleet","asset_id":"train-01",'
            b'"timestamp":"2026-09-23T10:00:00Z","value":132.6}',
            b'{"kind":"line_voltage","fleet_id":"demo-fleet","asset_id":"train-01",'
            b'"timestamp":"2026-09-23T10:00:01Z","value":26680}',
        )
        reading_ids = iter((
            "123e4567-e89b-12d3-a456-426614174000",
            "123e4567-e89b-12d3-a456-426614174001",
        ))
        staging = RecordingStaging()
        queues = RecordingQueues()

        responses = [
            handle_post_readings(
                body,
                "application/json",
                staging,
                queues,
                id_factory=reading_ids.__next__,
            )
            for body in readings
        ]

        self.assertEqual([response.status_code for response in responses], [202, 202])
        self.assertEqual(len(staging.objects), 2)
        self.assertEqual([kind for kind, _ in queues.messages], ["speed", "line_voltage"])

    def test_invalid_requests_are_rejected_without_side_effects(self):
        valid = {
            "kind": "speed",
            "fleet_id": "demo-fleet",
            "asset_id": "train-01",
            "timestamp": "2026-09-23T10:00:00Z",
            "value": 132.6,
        }
        cases = (
            (b"{}", None, 415),
            (b"{}", "text/plain", 415),
            (b"\xff", "application/json", 400),
            (b"{broken", "application/json", 400),
            (b"[]", "application/json", 400),
            (b'{"value":NaN}', "application/json", 400),
            (b'{"value":Infinity}', "application/json", 400),
            (json.dumps({**valid, "kind": "temperature"}).encode(), "application/json", 422),
            (json.dumps({key: value for key, value in valid.items() if key != "kind"}).encode(), "application/json", 422),
            (json.dumps({**valid, "value": -1}).encode(), "application/json", 422),
            (json.dumps({**valid, "value": True}).encode(), "application/json", 422),
            (b'{"kind":"speed","fleet_id":"f","asset_id":"a","timestamp":"2026-02-30T10:00:00Z","value":1}', "application/json", 422),
            (b'{"kind":"speed","fleet_id":"f","asset_id":"a","timestamp":"2026-09-23T10:00:00+00:00","value":1}', "application/json", 422),
            (json.dumps({**valid, "extra": "not allowed"}).encode(), "application/json", 422),
            (json.dumps({**valid, "fleet_id": ""}).encode(), "application/json", 422),
            (json.dumps({**valid, "asset_id": "a" * 129}).encode(), "application/json", 422),
            (json.dumps({**valid, "value": 10**500}).encode(), "application/json", 422),
            (b'{"kind":"speed","fleet_id":"f","asset_id":"a","timestamp":"2026-09-23T10:00:00Z","value":1e999}', "application/json", 422),
        )

        for body, content_type, expected_status in cases:
            with self.subTest(body=body, content_type=content_type):
                events = []
                staging = RecordingStaging(events)
                queues = RecordingQueues(events)
                id_calls = []

                response = handle_post_readings(
                    body,
                    content_type,
                    staging,
                    queues,
                    id_factory=lambda: id_calls.append(True),
                )

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(events, [])
                self.assertEqual(id_calls, [])

    def test_body_limit_is_enforced_in_bytes(self):
        body = (
            b'{"kind":"speed","fleet_id":"f","asset_id":"a",'
            b'"timestamp":"2026-09-23T10:00:00Z","value":1}'
        )
        exact_limit_body = body + b" " * (4096 - len(body))
        staging = RecordingStaging()
        queues = RecordingQueues()

        response = handle_post_readings(
            exact_limit_body,
            "application/json; charset=utf-8",
            staging,
            queues,
            id_factory=lambda: "123e4567-e89b-12d3-a456-426614174000",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(next(iter(staging.objects.values()))[0], exact_limit_body)

        oversized = handle_post_readings(
            exact_limit_body + b" ",
            "application/json",
            RecordingStaging(),
            RecordingQueues(),
        )
        self.assertEqual(oversized.status_code, 413)

    def test_staging_failure_does_not_publish(self):
        events = []
        staging = RecordingStaging(events, fail=True)
        queues = RecordingQueues(events)

        response = handle_post_readings(
            b'{"kind":"speed","fleet_id":"f","asset_id":"a","timestamp":"2026-09-23T10:00:00Z","value":1}',
            "application/json",
            staging,
            queues,
            id_factory=lambda: "123e4567-e89b-12d3-a456-426614174000",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(events, [("stage", "raw/123e4567-e89b-12d3-a456-426614174000.json")])
        self.assertEqual(queues.messages, [])

    def test_publish_failure_retains_staged_object(self):
        events = []
        staging = RecordingStaging(events)
        queues = RecordingQueues(events, fail=True)
        staging_key = "raw/123e4567-e89b-12d3-a456-426614174000.json"

        with self.assertLogs("backend.src.ingestion", level="ERROR") as logs:
            response = handle_post_readings(
                b'{"kind":"speed","fleet_id":"f","asset_id":"a","timestamp":"2026-09-23T10:00:00Z","value":1}',
                "application/json",
                staging,
                queues,
                id_factory=lambda: "123e4567-e89b-12d3-a456-426614174000",
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn(staging_key, staging.objects)
        self.assertEqual(events, [("stage", staging_key), ("publish", "speed")])
        self.assertIn("123e4567-e89b-12d3-a456-426614174000", logs.output[0])
        self.assertIn(staging_key, logs.output[0])


if __name__ == "__main__":
    unittest.main()