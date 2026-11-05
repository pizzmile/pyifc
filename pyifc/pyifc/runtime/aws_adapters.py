from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from typing import Any

from pyifc.core.binding import bind


class S3Bucket:
    # Actions beyond the capability policy grants. Empty: each call below is covered by it.
    REQUIRED_ACTIONS: dict[str, tuple[str, ...]] = {}

    def __init__(self, client: Any, name: str) -> None:
        self.client = client
        self.name = name

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.name, Key=key, Body=body, ContentType=content_type)

    def get(self, key: str) -> tuple[bytes, str]:
        response = self.client.get_object(Bucket=self.name, Key=key)
        return response["Body"].read(), response.get("ContentType", "")

    def list(self, prefix: str = "") -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.name, Prefix=prefix
        )
        return sorted(
            item["Key"]
            for page in pages
            for item in page.get("Contents", [])
        )


class SQSQueue:
    # Actions beyond the capability policy grants. Empty: each call below is covered by it.
    REQUIRED_ACTIONS: dict[str, tuple[str, ...]] = {}

    def __init__(self, client: Any, url: str) -> None:
        self.client = client
        self.url = url

    def publish(self, message: bytes) -> None:
        self.client.send_message(QueueUrl=self.url, MessageBody=message.decode("utf-8"))


class DynamoDBTable:
    """Key-value adapter: hash key ``pk`` (string), value in binary attribute ``value``."""

    # Actions beyond the capability policy grants. Empty: each call below is covered by it.
    REQUIRED_ACTIONS: dict[str, tuple[str, ...]] = {}

    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    def get(self, key: str) -> bytes:
        response = self.client.get_item(TableName=self.table, Key={"pk": {"S": key}})
        item = response.get("Item")
        if item is None:
            raise KeyError(key)
        return bytes(item["value"]["B"])

    def put(self, key: str, value: bytes) -> None:
        self.client.put_item(
            TableName=self.table, Item={"pk": {"S": key}, "value": {"B": value}}
        )

    def delete(self, key: str) -> None:
        self.client.delete_item(TableName=self.table, Key={"pk": {"S": key}})


class SNSTopic:
    # Actions beyond the capability policy grants. Empty: each call below is covered by it.
    REQUIRED_ACTIONS: dict[str, tuple[str, ...]] = {}

    def __init__(self, client: Any, arn: str) -> None:
        self.client = client
        self.arn = arn

    def publish(self, message: bytes) -> None:
        self.client.publish(TopicArn=self.arn, Message=message.decode("utf-8"))


class SecretsManagerSecret:
    # Actions beyond the capability policy grants. Empty: each call below is covered by it.
    REQUIRED_ACTIONS: dict[str, tuple[str, ...]] = {}

    def __init__(self, client: Any, secret_id: str) -> None:
        self.client = client
        self.secret_id = secret_id

    def get(self) -> str:
        return self.client.get_secret_value(SecretId=self.secret_id)["SecretString"]


def bind_from_environment(resources: list[Any]) -> dict[str, Any]:
    import boto3
    from botocore.config import Config

    bindings: dict[str, Any] = {}
    for resource in resources:
        slug = resource.id.upper().replace("-", "_")
        if resource.kind == "object_store":
            client = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                config=Config(s3={"addressing_style": "path"}),
            )
            bindings[resource.id] = S3Bucket(client, os.environ[f"PYIFC_BUCKET_{slug}"])
        elif resource.kind == "queue":
            client = boto3.client("sqs", endpoint_url=os.environ.get("SQS_ENDPOINT_URL"))
            bindings[resource.id] = SQSQueue(client, os.environ[f"PYIFC_QUEUE_{slug}"])
        elif resource.kind == "key_value_store":
            client = boto3.client("dynamodb", endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"))
            bindings[resource.id] = DynamoDBTable(client, os.environ[f"PYIFC_TABLE_{slug}"])
        elif resource.kind == "topic":
            client = boto3.client("sns", endpoint_url=os.environ.get("SNS_ENDPOINT_URL"))
            bindings[resource.id] = SNSTopic(client, os.environ[f"PYIFC_TOPIC_{slug}"])
        elif resource.kind == "secret":
            client = boto3.client(
                "secretsmanager", endpoint_url=os.environ.get("SECRETSMANAGER_ENDPOINT_URL")
            )
            bindings[resource.id] = SecretsManagerSecret(client, os.environ[f"PYIFC_SECRET_{slug}"])
    return bindings


def handle_post(
    function: Callable[..., dict[str, Any]],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> dict[str, Any]:
    body = event.get("body") or ""
    try:
        payload = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode("utf-8")
    except (ValueError, TypeError):
        payload = b""
    headers = event.get("headers") or {}
    content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"), None
    )
    try:
        with bind(adapters):
            result = function(payload, content_type)
        status_code = 202
    except Exception as error:
        if not all(hasattr(error, field) for field in ("status_code", "code", "message")):
            raise
        failure: Any = error
        status_code = failure.status_code
        result = {"error": {"code": failure.code, "message": failure.message}}
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(result, separators=(",", ":"), sort_keys=True),
    }


def handle_get(
    function: Callable[..., Any],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    parameters = {
        key: query[key]
        for key in ("limit", "fleet_id", "asset_id")
        if key in query
    }
    with bind(adapters):
        result = function(**parameters)
    status_code = getattr(result, "status_code", 200)
    body = getattr(result, "body", result)
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, separators=(",", ":"), sort_keys=True),
    }


def handle_sqs(
    function: Callable[[bytes], Any],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> None:
    with bind(adapters):
        for record in event.get("Records", []):
            function(record["body"].encode("utf-8"))

def handle_sns(
    function: Callable[[bytes], Any],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> None:
    with bind(adapters):
        for record in event.get("Records", []):
            function(record["Sns"]["Message"].encode("utf-8"))


def handle_worker_event(
    function: Callable[[bytes], Any],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> Any:
    """Dispatch a worker invocation by event source: SQS batch or SNS notification."""
    records = event.get("Records", [])
    if records and all("Sns" in record for record in records):
        return handle_sns(function, event, adapters)
    return handle_sqs(function, event, adapters)


def handle_schedule(
    function: Callable[[], Any],
    event: dict[str, Any],
    adapters: dict[str, Any],
) -> Any:
    """Run a scheduled job; the EventBridge event carries nothing the job needs."""
    with bind(adapters):
        return function()
