import json
import os
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config


class S3ObjectStore:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            config=Config(s3={"addressing_style": "path"}),
        )

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
        )

    def get(self, key: str) -> tuple[bytes, str]:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read(), response.get("ContentType", "")

    def list_keys(self) -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket
        )
        return [item["Key"] for page in pages for item in page.get("Contents", [])]


@dataclass(frozen=True)
class SQSMessage:
    job: dict
    receipt_handle: str


class SQSQueues:
    QUEUE_NAMES = {
        "speed": "sensor-hub-speed",
        "line_voltage": "sensor-hub-line-voltage",
    }

    def __init__(
        self,
        endpoint_url: str | None = None,
        client=None,
        queue_urls: dict[str, str] | None = None,
        create_missing: bool = False,
    ):
        self.client = client or boto3.client(
            "sqs",
            endpoint_url=endpoint_url or os.environ.get("SQS_ENDPOINT_URL"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
        self._queue_urls = dict(queue_urls or {})
        self._create_missing = create_missing

    def ensure_queue(self, kind: str) -> None:
        self._queue_url(kind)

    def publish(self, kind: str, job: dict) -> None:
        self.client.send_message(
            QueueUrl=self._queue_url(kind), MessageBody=self._encode(job)
        )

    def reserve(self, kind: str, timeout: int = 5) -> SQSMessage | None:
        response = self.client.receive_message(
            QueueUrl=self._queue_url(kind),
            MaxNumberOfMessages=1,
            WaitTimeSeconds=min(max(timeout, 0), 20),
            VisibilityTimeout=60,
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        return SQSMessage(
            job=json.loads(message["Body"]),
            receipt_handle=message["ReceiptHandle"],
        )

    def acknowledge(self, kind: str, message: SQSMessage) -> None:
        self.client.delete_message(
            QueueUrl=self._queue_url(kind), ReceiptHandle=message.receipt_handle
        )

    def retry(self, kind: str, message: SQSMessage) -> None:
        self.client.change_message_visibility(
            QueueUrl=self._queue_url(kind),
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=5,
        )

    def _queue_url(self, kind: str) -> str:
        try:
            queue_name = self.QUEUE_NAMES[kind]
        except KeyError:
            raise ValueError(f"Unsupported queue kind: {kind}") from None
        if kind not in self._queue_urls:
            try:
                response = self.client.get_queue_url(QueueName=queue_name)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if not self._create_missing or code not in {
                    "AWS.SimpleQueueService.NonExistentQueue",
                    "QueueDoesNotExist",
                }:
                    raise
                response = self.client.create_queue(QueueName=queue_name)
            self._queue_urls[kind] = response["QueueUrl"]
        return self._queue_urls[kind]

    @staticmethod
    def _encode(job: dict) -> str:
        return json.dumps(job, separators=(",", ":"), sort_keys=True)