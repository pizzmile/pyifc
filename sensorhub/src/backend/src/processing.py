import json
from typing import Protocol

from .schemas import (
    InvalidSchemaValue,
    validate_job,
    validate_processed_reading,
    validate_staged_reading,
)


class ObjectReader(Protocol):
    def get(self, key: str) -> tuple[bytes, str]: ...


class ObjectWriter(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> None: ...


class InvalidJob(ValueError):
    pass


class InvalidStagedReading(ValueError):
    pass


class StagedObjectUnavailable(RuntimeError):
    pass


def process_speed_job(job: dict, staging: ObjectReader, results: ObjectWriter) -> None:
    _process_job(job, staging, results, "speed", "km/h", "stopped", "moving")


def process_line_voltage_job(
    job: dict, staging: ObjectReader, results: ObjectWriter
) -> None:
    _process_job(
        job,
        staging,
        results,
        "line_voltage",
        "V",
        "no_voltage_observed",
        "voltage_observed",
    )


def _process_job(
    job: dict,
    staging: ObjectReader,
    results: ObjectWriter,
    expected_kind: str,
    unit: str,
    zero_status: str,
    positive_status: str,
) -> None:
    validate_processing_job(job)
    try:
        body, content_type = staging.get(job["staging_key"])
    except Exception:
        raise StagedObjectUnavailable from None
    result_key, result_body = prepare_processed_reading(
        job,
        body,
        content_type,
        expected_kind,
        unit,
        zero_status,
        positive_status,
    )
    results.put(result_key, result_body, "application/json")


def validate_processing_job(job: dict) -> None:
    try:
        validate_job(job)
    except (InvalidSchemaValue, TypeError):
        raise InvalidJob from None


def prepare_processed_reading(
    job: dict,
    body: bytes,
    content_type: str,
    expected_kind: str,
    unit: str,
    zero_status: str,
    positive_status: str,
) -> tuple[str, bytes]:
    try:
        if content_type != "application/json":
            raise InvalidSchemaValue
        reading = json.loads(body)
        validate_staged_reading(reading, expected_kind)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidSchemaValue, TypeError):
        raise InvalidStagedReading from None

    processed = {
        "id": job["id"],
        "kind": expected_kind,
        "fleet_id": reading["fleet_id"],
        "asset_id": reading["asset_id"],
        "timestamp": reading["timestamp"],
        "value": reading["value"],
        "unit": unit,
        "status": zero_status if reading["value"] == 0 else positive_status,
    }
    validate_processed_reading(processed)
    return (
        f'{job["id"]}.json',
        json.dumps(processed, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )