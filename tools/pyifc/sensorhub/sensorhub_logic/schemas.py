import json
import math
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
SCHEMAS = {
    path.name.removesuffix(".schema.json"): json.loads(path.read_text(encoding="utf-8"))
    for path in SCHEMA_DIR.glob("*.schema.json")
}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
VALIDATORS = {
    name: Draft202012Validator(
        schema, registry=REGISTRY, format_checker=FormatChecker()
    )
    for name, schema in SCHEMAS.items()
}
UPLOAD_SCHEMAS = {
    "speed": "speed-reading",
    "line_voltage": "line-voltage-reading",
}


class UnsupportedReadingKind(ValueError):
    pass


class InvalidSchemaValue(ValueError):
    pass


def _validate_reading(reading, expected_kind):
    schema_name = UPLOAD_SCHEMAS.get(expected_kind)
    if schema_name is None:
        raise UnsupportedReadingKind

    errors = list(VALIDATORS[schema_name].iter_errors(reading))
    if errors:
        raise InvalidSchemaValue

    try:
        finite_value = math.isfinite(reading["value"])
    except OverflowError:
        finite_value = False
    if not finite_value:
        raise InvalidSchemaValue

    timestamp = reading["timestamp"]
    try:
        if not timestamp.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(f"{timestamp[:-1]}+00:00")
    except (AttributeError, ValueError):
        raise InvalidSchemaValue from None


def validate_upload(reading):
    kind = reading.get("kind")
    _validate_reading(reading, kind)

    return kind


def validate_staged_reading(reading, expected_kind):
    _validate_reading(reading, expected_kind)


def validate_job(job):
    if list(VALIDATORS["reading-job"].iter_errors(job)):
        raise InvalidSchemaValue


def validate_processed_reading(reading):
    if list(VALIDATORS["processed-reading"].iter_errors(reading)):
        raise InvalidSchemaValue