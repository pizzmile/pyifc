from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable

from sensorhub_logic.ingestion import PublishingUnavailable, StagingUnavailable, prepare_reading
from sensorhub_logic.http_types import HttpResponse
from sensorhub_logic.processing import (
    StagedObjectUnavailable,
    prepare_processed_reading,
    validate_processing_job,
)
from sensorhub_logic.query import handle_get_readings_from_items, parse_processed_item
from pyifc import Application, Bucket, Topic


app = Application("sensorhub")

staging = Bucket("sensorhub-staging")
results = Bucket("sensorhub-results")
speed_readings = Topic("sensorhub-speed")
line_voltage_readings = Topic("sensorhub-line-voltage")


@app.handler(method="POST", path="/readings", id="ingestion")
@app.write(staging)
@app.publish(speed_readings)
@app.publish(line_voltage_readings)
def ingest_reading(
    body: bytes,
    content_type: str | None,
    *,
    id_factory: Callable[[], object] = uuid.uuid4,
) -> dict[str, str]:
    kind, staging_key, job = prepare_reading(body, content_type, id_factory=id_factory)
    try:
        staging.put(staging_key, body)
    except Exception:
        logging.getLogger(__name__).exception("Staging write failed for reading_id=%s", job["id"])
        raise StagingUnavailable from None
    message = json.dumps(job, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        if kind == "speed":
            speed_readings.publish(message)
        else:
            line_voltage_readings.publish(message)
    except Exception:
        logging.getLogger(__name__).exception("Topic publish failed for reading_id=%s", job["id"])
        raise PublishingUnavailable from None
    return {"id": job["id"]}


@app.event_handler(id="speed-parser")
@app.subscribe(speed_readings)
@app.read(staging)
@app.write(results)
def parse_speed(message: bytes) -> None:
    job = json.loads(message)
    validate_processing_job(job)
    try:
        body, content_type = staging.get(job["staging_key"])
    except Exception:
        raise StagedObjectUnavailable from None
    key, processed = prepare_processed_reading(
        job, body, content_type, "speed", "km/h", "stopped", "moving"
    )
    results.put(key, processed)


@app.event_handler(id="line-voltage-parser")
@app.subscribe(line_voltage_readings)
@app.read(staging)
@app.write(results)
def parse_line_voltage(message: bytes) -> None:
    job = json.loads(message)
    validate_processing_job(job)
    try:
        body, content_type = staging.get(job["staging_key"])
    except Exception:
        raise StagedObjectUnavailable from None
    key, processed = prepare_processed_reading(
        job, body, content_type, "line_voltage", "V",
        "no_voltage_observed", "voltage_observed"
    )
    results.put(key, processed)


@app.handler(method="GET", path="/readings", id="query")
@app.list(results)
@app.read(results)
def query_readings(
    limit: int | str = 50,
    fleet_id: str | None = None,
    asset_id: str | None = None,
) -> HttpResponse:
    items = []
    for key in results.list(""):
        item = parse_processed_item(*results.get(key))
        if item is not None:
            items.append(item)
    return handle_get_readings_from_items(items, limit, fleet_id, asset_id)
