import io

import pytest

from pyifc.authoring.resources import Bucket
from pyifc.runtime.aws_adapters import S3Bucket, handle_get, handle_sqs


class RecordingS3Client:
    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(b"payload"), "ContentType": "application/json"}

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return RecordingPaginator()


class RecordingPaginator:
    def paginate(self, **kwargs):
        assert kwargs == {"Bucket": "staging", "Prefix": "raw/"}
        return [
            {"Contents": [{"Key": "raw/2.json"}]},
            {"Contents": [{"Key": "raw/1.json"}]},
        ]


def test_s3_bucket_reads_body_and_content_type():
    bucket = S3Bucket(RecordingS3Client(), "staging")

    assert bucket.get("raw/reading.json") == (b"payload", "application/json")


def test_s3_bucket_lists_all_pages_with_prefix():
    bucket = S3Bucket(RecordingS3Client(), "staging")

    assert bucket.list("raw/") == ["raw/1.json", "raw/2.json"]


def test_get_adapter_forwards_api_gateway_query_parameters_and_response():
    seen = []

    def query(**kwargs):
        seen.append(kwargs)
        return type("Response", (), {
            "status_code": 200,
            "body": {"readings": []},
        })()

    response = handle_get(
        query,
        {"queryStringParameters": {"limit": "12", "fleet_id": "fleet-a", "asset_id": "train-1"}},
        {},
    )

    assert seen == [{"limit": "12", "fleet_id": "fleet-a", "asset_id": "train-1"}]
    assert response == {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": '{"readings":[]}',
    }


def test_sqs_adapter_dispatches_record_bodies_with_scoped_bindings():
    bucket = Bucket("staging")
    seen = []

    class Store:
        def get(self, key):
            return b"payload", "application/json"

    def worker(message):
        seen.append((message, bucket.get("raw/reading.json")))

    handle_sqs(
        worker,
        {"Records": [{"body": "{\"id\":\"reading\"}"}]},
        {"staging": Store()},
    )

    assert seen == [(b'{"id":"reading"}', (b"payload", "application/json"))]


def test_sqs_adapter_propagates_worker_failure_for_retry():
    def worker(message):
        raise ValueError("bad job")

    with pytest.raises(ValueError, match="bad job"):
        handle_sqs(worker, {"Records": [{"body": "{}"}]}, {})
