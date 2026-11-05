import unittest

from backend.src.adapters import InMemoryObjectStore, InMemoryQueues


class AdapterTests(unittest.TestCase):
    def test_object_store_preserves_body_and_content_type(self):
        store = InMemoryObjectStore()

        store.put("raw/reading.json", b'{"value":1}', "application/json")

        self.assertEqual(
            store.get("raw/reading.json"),
            (b'{"value":1}', "application/json"),
        )
        self.assertEqual(store.list_keys(), ["raw/reading.json"])

    def test_queues_route_jobs_by_kind_and_preserve_fifo_order(self):
        queues = InMemoryQueues()
        first_speed = {"id": "first", "staging_key": "raw/first.json"}
        second_speed = {"id": "second", "staging_key": "raw/second.json"}
        voltage = {"id": "voltage", "staging_key": "raw/voltage.json"}

        queues.publish("speed", first_speed)
        queues.publish("line_voltage", voltage)
        queues.publish("speed", second_speed)

        self.assertEqual(queues.consume("speed"), first_speed)
        self.assertEqual(queues.consume("speed"), second_speed)
        self.assertEqual(queues.consume("line_voltage"), voltage)

    def test_queues_reject_unknown_kinds(self):
        queues = InMemoryQueues()

        with self.assertRaisesRegex(ValueError, "Unsupported queue kind"):
            queues.publish("temperature", {})


if __name__ == "__main__":
    unittest.main()