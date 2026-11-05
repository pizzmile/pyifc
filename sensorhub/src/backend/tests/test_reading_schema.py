import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"
SCHEMAS = {
    path.name.removesuffix(".schema.json"): json.loads(path.read_text(encoding="utf-8"))
    for path in SCHEMA_DIR.glob("*.schema.json")
}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def validator(name):
    return Draft202012Validator(
        SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker()
    )


class ReadingSchemaTests(unittest.TestCase):
    def setUp(self):
        self.speed = {
            "kind": "speed",
            "fleet_id": "demo-fleet",
            "asset_id": "train-01",
            "timestamp": "2026-09-23T10:00:00Z",
            "value": 132.6,
        }
        self.voltage = {**self.speed, "kind": "line_voltage", "value": 26680}

    def test_all_schemas_are_valid(self):
        for schema in SCHEMAS.values():
            Draft202012Validator.check_schema(schema)

    def test_each_parser_accepts_only_its_own_kind(self):
        for name, reading, other in (
            ("speed-reading", self.speed, self.voltage),
            ("line-voltage-reading", self.voltage, self.speed),
        ):
            with self.subTest(schema=name):
                self.assertTrue(validator(name).is_valid(reading))
                self.assertFalse(validator(name).is_valid(other))

    def test_invalid_upload_fields_are_rejected(self):
        for change in (
            {"value": -1},
            {"value": True},
            {"value": "132.6"},
            {"timestamp": "not-a-date"},
            {"timestamp": "2026-09-23T10:00:00+00:00"},
            {"fleet_id": ""},
            {"unexpected": 1},
        ):
            with self.subTest(change=change):
                self.assertFalse(validator("speed-reading").is_valid({**self.speed, **change}))

    def test_job_and_processed_readings(self):
        reading_id = "123e4567-e89b-12d3-a456-426614174000"
        job = {"id": reading_id, "staging_key": f"raw/{reading_id}.json"}
        self.assertTrue(validator("reading-job").is_valid(job))

        processed_speed = {**self.speed, "id": reading_id, "unit": "km/h", "status": "moving"}
        processed_voltage = {
            **self.voltage,
            "id": "123e4567-e89b-12d3-a456-426614174001",
            "unit": "V",
            "status": "voltage_observed",
        }
        self.assertTrue(validator("processed-reading").is_valid(processed_speed))
        self.assertTrue(validator("processed-reading").is_valid(processed_voltage))
        self.assertTrue(validator("readings-response").is_valid({"readings": [processed_speed, processed_voltage]}))

        for invalid in (
            {**processed_speed, "unit": "V"},
            {**processed_speed, "status": "stopped"},
            {**processed_voltage, "status": "moving"},
            {**processed_voltage, "value": 0},
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(validator("processed-reading").is_valid(invalid))

    def test_zero_values_use_kind_specific_statuses(self):
        for reading, unit, status in (
            (self.speed, "km/h", "stopped"),
            (self.voltage, "V", "no_voltage_observed"),
        ):
            processed = {
                **reading,
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "value": 0,
                "unit": unit,
                "status": status,
            }
            self.assertTrue(validator("processed-reading").is_valid(processed))


if __name__ == "__main__":
    unittest.main()