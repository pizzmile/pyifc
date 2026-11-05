import json

from .schemas import (
    InvalidSchemaValue,
    validate_job,
    validate_processed_reading,
    validate_staged_reading,
)


class InvalidJob(ValueError):
    pass


class InvalidStagedReading(ValueError):
    pass


class StagedObjectUnavailable(RuntimeError):
    pass


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