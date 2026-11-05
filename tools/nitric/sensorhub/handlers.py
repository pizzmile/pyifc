"""Nitric glue around the vanilla modules in core/.

core/ holds the transport-independent logic copied from the vanilla backend
(validation, job/result building, query ordering). This file only adapts it to
Nitric's async bucket/topic clients and HTTP context.
"""
import json

from core.ingestion import RequestError, prepare_reading
from core.processing import (
    prepare_processed_reading,
    validate_processing_job,
)
from core.query import get_recent_readings_from_items, invalid_query_response, parse_processed_item


def _reply(ctx, status, body):
    ctx.res.status = status
    ctx.res.headers["Content-Type"] = ["application/json"]
    ctx.res.body = json.dumps(body, separators=(",", ":"))
    return ctx


def _header(ctx, name):
    for key, value in ctx.req.headers.items():
        if key.lower() == name:
            return value[0] if isinstance(value, list) else value
    return None


async def ingest(ctx, staging, publishers):
    """POST /readings. publishers maps kind -> topic ref."""
    body = ctx.req.data
    if isinstance(body, str):
        body = body.encode("utf-8")
    try:
        kind, staging_key, job = prepare_reading(body or b"", _header(ctx, "content-type"))
    except RequestError as error:
        return _reply(ctx, error.status_code, {"error": {"code": error.code, "message": error.message}})
    await staging.file(staging_key).write(body)
    await publishers[kind].publish(job)
    return _reply(ctx, 202, {"id": job["id"]})


async def process(ctx, staging, results, kind, unit, zero_status, positive_status):
    """Topic subscriber body. Raising makes the delivery fail."""
    job = ctx.req.data
    validate_processing_job(job)  # raises InvalidJob
    staged = await staging.file(job["staging_key"]).read()
    # Nitric buckets do not return a content type; staged objects are JSON by construction.
    key, body = prepare_processed_reading(
        job, staged, "application/json", kind, unit, zero_status, positive_status
    )
    await results.file(key).write(body)


async def query(ctx, results):
    """GET /readings with optional limit, fleet_id and asset_id."""
    def param(name, default=None):
        value = ctx.req.query.get(name, default)
        return value[0] if isinstance(value, list) else value

    limit, fleet_id, asset_id = param("limit", 50), param("fleet_id"), param("asset_id")
    items = []
    for ref in await results.files():
        item = parse_processed_item(await results.file(ref.key).read(), "application/json")
        if item is not None:
            items.append(item)
    try:
        return _reply(ctx, 200, get_recent_readings_from_items(items, limit, fleet_id, asset_id))
    except (TypeError, ValueError):
        response = invalid_query_response(limit, fleet_id, asset_id)
        return _reply(ctx, response.status_code, response.body)
