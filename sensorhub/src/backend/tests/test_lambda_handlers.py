import base64
import json
import unittest

from backend.src.adapters import InMemoryObjectStore, InMemoryQueues
from backend.src.lambda_handlers import (
    ingestion_handler,
    line_voltage_handler,
    query_handler,
    speed_handler,
)


SPEED_READING = {
    "kind": "speed",
    "fleet_id": "demo-fleet",
    "asset_id": "train-01",
    "timestamp": "2026-09-24T10:00:00Z",
    "value": 132.6,
}


class LambdaHandlerTests(unittest.TestCase):
    def test_ingestion_adapts_api_gateway_v2_request(self):
        staging = InMemoryObjectStore()
        queues = InMemoryQueues()
        encoded = base64.b64encode(json.dumps(SPEED_READING).encode()).decode()

        response = ingestion_handler(
            {
                "body": encoded,
                "isBase64Encoded": True,
                "headers": {"Content-Type": "application/json"},
            },
            None,
            staging=staging,
            publisher=queues,
        )

        self.assertEqual(response["statusCode"], 202)
        reading_id = json.loads(response["body"])["id"]
        job = queues.consume("speed")
        self.assertEqual(job["id"], reading_id)
        self.assertEqual(staging.get(job["staging_key"])[0], json.dumps(SPEED_READING).encode())

    def test_ingestion_rejects_invalid_base64_body(self):
        response = ingestion_handler(
            {
                "body": "not-base64!",
                "isBase64Encoded": True,
                "headers": {"content-type": "application/json"},
            },
            None,
            staging=InMemoryObjectStore(),
            publisher=InMemoryQueues(),
        )

        self.assertEqual(response["statusCode"], 400)

    def test_query_adapts_query_parameters(self):
        response = query_handler(
            {"queryStringParameters": {"limit": "all"}},
            None,
            results=InMemoryObjectStore(),
        )

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "invalid_limit")

    def test_query_forwards_train_filters(self):
        results = InMemoryObjectStore()
        results.put(
            "reading-1.json",
            json.dumps({"id": "reading-1", "fleet_id": "fleet-a", "asset_id": "train-1"}).encode(),
            "application/json",
        )
        results.put(
            "reading-2.json",
            json.dumps({"id": "reading-2", "fleet_id": "fleet-a", "asset_id": "train-2"}).encode(),
            "application/json",
        )

        response = query_handler(
            {"queryStringParameters": {"fleet_id": "fleet-a", "asset_id": "train-1"}},
            None,
            results=results,
        )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual([item["id"] for item in json.loads(response["body"])["readings"]], ["reading-1"])

    def test_queue_handlers_process_their_matching_kinds(self):
        staging = InMemoryObjectStore()
        results = InMemoryObjectStore()
        readings = [
            ("speed", SPEED_READING, speed_handler),
            (
                "line_voltage",
                {**SPEED_READING, "kind": "line_voltage", "value": 26680},
                line_voltage_handler,
            ),
        ]

        for index, (kind, reading, handler) in enumerate(readings, start=1):
            reading_id = f"00000000-0000-4000-8000-00000000000{index}"
            key = f"raw/{reading_id}.json"
            staging.put(key, json.dumps(reading).encode(), "application/json")
            event = {
                "Records": [
                    {
                        "body": json.dumps(
                            {"id": reading_id, "staging_key": key}
                        )
                    }
                ]
            }

            handler(event, None, staging=staging, results=results)

            processed = json.loads(results.get(f"{reading_id}.json")[0])
            self.assertEqual(processed["kind"], kind)

    def test_queue_handler_propagates_invalid_jobs_for_retry(self):
        with self.assertRaises(Exception):
            speed_handler(
                {"Records": [{"body": "{}"}]},
                None,
                staging=InMemoryObjectStore(),
                results=InMemoryObjectStore(),
            )


if __name__ == "__main__":
    unittest.main()