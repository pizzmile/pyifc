import json
import unittest

from botocore.exceptions import ClientError

from backend.src.container_adapters import SQSMessage, SQSQueues


class RecordingSQSClient:
    def __init__(self):
        self.calls = []

    def get_queue_url(self, **kwargs):
        self.calls.append(("get_queue_url", kwargs))
        return {"QueueUrl": f'http://floci:4566/000000000000/{kwargs["QueueName"]}'}

    def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))

    def receive_message(self, **kwargs):
        self.calls.append(("receive_message", kwargs))
        return {
            "Messages": [
                {
                    "Body": '{"id":"reading-1","staging_key":"raw/reading-1.json"}',
                    "ReceiptHandle": "receipt-1",
                }
            ]
        }

    def delete_message(self, **kwargs):
        self.calls.append(("delete_message", kwargs))

    def change_message_visibility(self, **kwargs):
        self.calls.append(("change_message_visibility", kwargs))


class SQSQueuesTests(unittest.TestCase):
    def test_publishes_receives_acknowledges_and_retries_sqs_messages(self):
        client = RecordingSQSClient()
        queues = SQSQueues(client=client)
        job = {"id": "reading-1", "staging_key": "raw/reading-1.json"}

        queues.publish("speed", job)
        received = queues.reserve("speed", timeout=7)

        self.assertEqual(received, SQSMessage(job=job, receipt_handle="receipt-1"))
        queues.acknowledge("speed", received)
        queues.retry("speed", received)

        self.assertEqual(
            [call[0] for call in client.calls],
            [
                "get_queue_url",
                "send_message",
                "receive_message",
                "delete_message",
                "change_message_visibility",
            ],
        )
        self.assertEqual(client.calls[0][1]["QueueName"], "sensor-hub-speed")
        self.assertEqual(client.calls[1][1]["MessageBody"], json.dumps(job, separators=(",", ":"), sort_keys=True))
        self.assertEqual(client.calls[2][1]["WaitTimeSeconds"], 7)
        self.assertEqual(client.calls[3][1]["ReceiptHandle"], "receipt-1")
        self.assertEqual(client.calls[4][1]["VisibilityTimeout"], 5)

    def test_uses_configured_queue_url_without_discovery(self):
        client = RecordingSQSClient()
        queue_url = "http://floci:4566/000000000000/configured-speed"
        queues = SQSQueues(client=client, queue_urls={"speed": queue_url})

        queues.publish("speed", {"id": "reading-1", "staging_key": "raw/1.json"})

        self.assertEqual([call[0] for call in client.calls], ["send_message"])
        self.assertEqual(client.calls[0][1]["QueueUrl"], queue_url)

    def test_does_not_create_a_missing_queue_by_default(self):
        class MissingQueueClient(RecordingSQSClient):
            def get_queue_url(self, **kwargs):
                raise ClientError(
                    {
                        "Error": {
                            "Code": "AWS.SimpleQueueService.NonExistentQueue",
                            "Message": "missing",
                        }
                    },
                    "GetQueueUrl",
                )

            def create_queue(self, **kwargs):
                self.calls.append(("create_queue", kwargs))

        client = MissingQueueClient()

        with self.assertRaises(ClientError):
            SQSQueues(client=client).ensure_queue("speed")

        self.assertNotIn("create_queue", [call[0] for call in client.calls])


if __name__ == "__main__":
    unittest.main()