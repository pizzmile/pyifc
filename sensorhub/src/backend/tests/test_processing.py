import json
import unittest

from backend.src.adapters import InMemoryObjectStore, InMemoryQueues
from backend.src.ingestion import handle_post_readings
from backend.src.processing import (
    InvalidJob,
    InvalidStagedReading,
    StagedObjectUnavailable,
    process_line_voltage_job,
    process_speed_job,
)
from backend.src.query import get_recent_readings, handle_get_readings


class ProcessingTests(unittest.TestCase):
    def test_speed_job_writes_normalized_result_by_reading_id(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174000"
        staging_key = f"raw/{reading_id}.json"
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()
        staging.put(
            staging_key,
            (
                b'{"kind":"speed","fleet_id":"demo-fleet",'
                b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
                b'"value":132.6}'
            ),
            "application/json",
        )

        process_speed_job(
            {"id": reading_id, "staging_key": staging_key},
            staging,
            results,
        )

        body, content_type = results.get(f"{reading_id}.json")
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {
                "id": reading_id,
                "kind": "speed",
                "fleet_id": "demo-fleet",
                "asset_id": "train-01",
                "timestamp": "2026-09-23T10:00:00Z",
                "value": 132.6,
                "unit": "km/h",
                "status": "moving",
            },
        )

    def test_line_voltage_job_uses_voltage_unit_and_zero_status(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174001"
        staging_key = f"raw/{reading_id}.json"
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()
        staging.put(
            staging_key,
            (
                b'{"kind":"line_voltage","fleet_id":"demo-fleet",'
                b'"asset_id":"train-02","timestamp":"2026-09-23T10:00:01Z",'
                b'"value":0}'
            ),
            "application/json",
        )

        process_line_voltage_job(
            {"id": reading_id, "staging_key": staging_key},
            staging,
            results,
        )

        body, _ = results.get(f"{reading_id}.json")
        processed = json.loads(body)
        self.assertEqual(processed["unit"], "V")
        self.assertEqual(processed["status"], "no_voltage_observed")

    def test_speed_parser_rejects_line_voltage_staged_input(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174002"
        staging_key = f"raw/{reading_id}.json"
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()
        staging.put(
            staging_key,
            (
                b'{"kind":"line_voltage","fleet_id":"demo-fleet",'
                b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
                b'"value":26680}'
            ),
            "application/json",
        )

        with self.assertRaises(InvalidStagedReading):
            process_speed_job(
                {"id": reading_id, "staging_key": staging_key},
                staging,
                results,
            )

        self.assertEqual(results.list_keys(), [])

    def test_parser_reports_missing_staged_object_without_writing_result(self):
        results = InMemoryObjectStore()

        with self.assertRaises(StagedObjectUnavailable):
            process_speed_job(
                {
                    "id": "123e4567-e89b-12d3-a456-426614174003",
                    "staging_key": "raw/missing.json",
                },
                InMemoryObjectStore(),
                results,
            )

        self.assertEqual(results.list_keys(), [])

    def test_parser_rejects_invalid_job_before_reading_staging(self):
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()

        with self.assertRaises(InvalidJob):
            process_speed_job(
                {"id": "not-a-uuid", "staging_key": "raw/reading.json"},
                staging,
                results,
            )

        self.assertEqual(results.list_keys(), [])

    def test_parser_rejects_malformed_staged_objects(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174004"
        job = {"id": reading_id, "staging_key": "raw/reading.json"}
        cases = (
            (b"{broken", "application/json"),
            (b"[]", "application/json"),
            (b'{}', "text/plain"),
        )

        for body, content_type in cases:
            with self.subTest(body=body, content_type=content_type):
                staging = InMemoryObjectStore()
                results = InMemoryObjectStore()
                staging.put(job["staging_key"], body, content_type)

                with self.assertRaises(InvalidStagedReading):
                    process_speed_job(job, staging, results)

                self.assertEqual(results.list_keys(), [])

    def test_retry_replaces_the_same_processed_object(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174005"
        staging_key = f"raw/{reading_id}.json"
        job = {"id": reading_id, "staging_key": staging_key}
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()
        staging.put(
            staging_key,
            (
                b'{"kind":"speed","fleet_id":"demo-fleet",'
                b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
                b'"value":0}'
            ),
            "application/json",
        )

        process_speed_job(job, staging, results)
        first_result = results.get(f"{reading_id}.json")
        process_speed_job(job, staging, results)

        self.assertEqual(results.list_keys(), [f"{reading_id}.json"])
        self.assertEqual(results.get(f"{reading_id}.json"), first_result)

    def test_local_flow_routes_both_kinds_to_their_matching_parsers(self):
        speed_id = "123e4567-e89b-12d3-a456-426614174006"
        voltage_id = "123e4567-e89b-12d3-a456-426614174007"
        speed_body = (
            b'{"kind":"speed","fleet_id":"demo-fleet",'
            b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
            b'"value":132.6}'
        )
        voltage_body = (
            b'{"kind":"line_voltage","fleet_id":"demo-fleet",'
            b'"asset_id":"train-02","timestamp":"2026-09-23T10:00:01Z",'
            b'"value":26680}'
        )
        reading_ids = iter((speed_id, voltage_id))
        staging = InMemoryObjectStore()
        queues = InMemoryQueues()
        results = InMemoryObjectStore()

        responses = [
            handle_post_readings(
                body,
                "application/json",
                staging,
                queues,
                id_factory=reading_ids.__next__,
            )
            for body in (speed_body, voltage_body)
        ]
        process_speed_job(queues.consume("speed"), staging, results)
        process_line_voltage_job(
            queues.consume("line_voltage"), staging, results
        )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        self.assertEqual(staging.get(f"raw/{speed_id}.json")[0], speed_body)
        self.assertEqual(staging.get(f"raw/{voltage_id}.json")[0], voltage_body)
        processed = [
            json.loads(results.get(key)[0]) for key in results.list_keys()
        ]
        self.assertEqual(
            [(reading["kind"], reading["status"]) for reading in processed],
            [("speed", "moving"), ("line_voltage", "voltage_observed")],
        )

    def test_end_to_end_readings_endpoint_returns_newest_normalized_results(self):
        speed_id = "123e4567-e89b-12d3-a456-426614174008"
        voltage_id = "123e4567-e89b-12d3-a456-426614174009"
        readings = (
            (
                speed_id,
                b'{"kind":"speed","fleet_id":"demo-fleet",'
                b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:00Z",'
                b'"value":132.6}',
            ),
            (
                voltage_id,
                b'{"kind":"line_voltage","fleet_id":"demo-fleet",'
                b'"asset_id":"train-01","timestamp":"2026-09-23T10:00:01Z",'
                b'"value":26680}',
            ),
        )
        reading_ids = iter(reading_id for reading_id, _ in readings)
        staging = InMemoryObjectStore()
        queues = InMemoryQueues()
        results = InMemoryObjectStore()

        for _, body in readings:
            accepted = handle_post_readings(
                body,
                "application/json",
                staging,
                queues,
                id_factory=reading_ids.__next__,
            )
            self.assertEqual(accepted.status_code, 202)

        process_speed_job(queues.consume("speed"), staging, results)
        process_line_voltage_job(queues.consume("line_voltage"), staging, results)

        response = handle_get_readings(results)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.body["readings"]],
            [voltage_id, speed_id],
        )
        self.assertEqual(
            [
                (item["kind"], item["value"], item["unit"], item["status"])
                for item in response.body["readings"]
            ],
            [
                ("line_voltage", 26680, "V", "voltage_observed"),
                ("speed", 132.6, "km/h", "moving"),
            ],
        )

    def test_get_recent_readings_sorts_by_timestamp_then_id_and_applies_limits(self):
        results = InMemoryObjectStore()
        baseline = [
            {
                "id": "a-1",
                "kind": "speed",
                "fleet_id": "f",
                "asset_id": "t",
                "timestamp": "2026-09-23T10:00:00Z",
                "value": 10,
                "unit": "km/h",
                "status": "moving",
            },
            {
                "id": "b-2",
                "kind": "line_voltage",
                "fleet_id": "f",
                "asset_id": "u",
                "timestamp": "2026-09-23T10:00:01Z",
                "value": 401,
                "unit": "V",
                "status": "voltage_observed",
            },
            {
                "id": "c-3",
                "kind": "speed",
                "fleet_id": "f",
                "asset_id": "v",
                "timestamp": "2026-09-23T10:00:01Z",
                "value": 0,
                "unit": "km/h",
                "status": "stopped",
            },
        ]

        for item in baseline:
            results.put(
                f"{item['id']}.json",
                json.dumps(item, separators=(",", ":")).encode("utf-8"),
                "application/json",
            )

        response = get_recent_readings(results)
        self.assertEqual(
            [item["id"] for item in response["readings"]],
            ["c-3", "b-2", "a-1"],
        )

        limited = get_recent_readings(results, limit=2)
        self.assertEqual([item["id"] for item in limited["readings"]], ["c-3", "b-2"])

        self.assertEqual(get_recent_readings(results, limit=0), {"readings": []})
        self.assertEqual(handle_get_readings(results, limit="invalid").status_code, 400)

        many_results = InMemoryObjectStore()
        for index in range(101):
            item = {
                "id": f"reading-{index:03}",
                "kind": "speed",
                "fleet_id": "f",
                "asset_id": "t",
                "timestamp": "2026-09-23T10:00:00Z",
                "value": 1,
                "unit": "km/h",
                "status": "moving",
            }
            many_results.put(
                f"{item['id']}.json",
                json.dumps(item).encode("utf-8"),
                "application/json",
            )

        capped = get_recent_readings(many_results, limit=200)
        self.assertEqual(len(capped["readings"]), 100)


if __name__ == "__main__":
    unittest.main()