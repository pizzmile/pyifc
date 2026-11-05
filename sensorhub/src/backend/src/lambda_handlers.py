import base64
import json
import os

from .container_adapters import S3ObjectStore, SQSQueues
from .ingestion import handle_post_readings
from .processing import process_line_voltage_job, process_speed_job
from .query import handle_get_readings


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, separators=(",", ":"), sort_keys=True),
    }


def _request_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body, validate=True)
    return body.encode("utf-8")


def _header(event: dict, name: str) -> str | None:
    expected = name.lower()
    for key, value in (event.get("headers") or {}).items():
        if key.lower() == expected:
            return value
    return None


def _queues() -> SQSQueues:
    queue_urls = {
        kind: url
        for kind, url in {
            "speed": os.environ.get("SPEED_QUEUE_URL"),
            "line_voltage": os.environ.get("LINE_VOLTAGE_QUEUE_URL"),
        }.items()
        if url
    }
    return SQSQueues(queue_urls=queue_urls)


def ingestion_handler(event, context, *, staging=None, publisher=None):
    staging = staging or S3ObjectStore(os.environ["STAGING_BUCKET"])
    publisher = publisher or _queues()
    try:
        body = _request_body(event)
    except (ValueError, TypeError):
        body = b""
    result = handle_post_readings(
        body,
        _header(event, "content-type"),
        staging,
        publisher,
    )
    return _response(result.status_code, result.body)


def query_handler(event, context, *, results=None):
    results = results or S3ObjectStore(os.environ["RESULTS_BUCKET"])
    query = event.get("queryStringParameters") or {}
    result = handle_get_readings(
        results,
        query.get("limit", 50),
        query.get("fleet_id"),
        query.get("asset_id"),
    )
    return _response(result.status_code, result.body)


def speed_handler(event, context, *, staging=None, results=None):
    staging = staging or S3ObjectStore(os.environ["STAGING_BUCKET"])
    results = results or S3ObjectStore(os.environ["RESULTS_BUCKET"])
    for record in event.get("Records", []):
        process_speed_job(json.loads(record["body"]), staging, results)


def line_voltage_handler(event, context, *, staging=None, results=None):
    staging = staging or S3ObjectStore(os.environ["STAGING_BUCKET"])
    results = results or S3ObjectStore(os.environ["RESULTS_BUCKET"])
    for record in event.get("Records", []):
        process_line_voltage_job(json.loads(record["body"]), staging, results)