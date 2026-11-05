import json
from collections.abc import Iterable

from .http_types import HttpResponse


def parse_processed_item(body: bytes, content_type: str):
    if content_type != "application/json":
        return None
    try:
        item = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(item, dict):
        return None
    return item


def get_recent_readings_from_items(
    items: Iterable[dict],
    limit: int | str = 50,
    fleet_id: str | None = None,
    asset_id: str | None = None,
) -> dict:
    if (fleet_id is None) != (asset_id is None):
        raise ValueError("fleet_id and asset_id must be provided together")
    if fleet_id is not None and (not fleet_id.strip() or not asset_id.strip()):
        raise ValueError("fleet_id and asset_id must not be empty")

    requested_limit = int(limit)
    if requested_limit < 0:
        requested_limit = 0
    effective_limit = min(requested_limit, 100)

    filtered = [
        item
        for item in items
        if fleet_id is None
        or (item.get("fleet_id") == fleet_id and item.get("asset_id") == asset_id)
    ]

    ordered = sorted(
        filtered,
        key=lambda item: (item.get("timestamp", ""), item.get("id", "")),
        reverse=True,
    )
    return {"readings": ordered[:effective_limit]}


def handle_get_readings_from_items(
    items: Iterable[dict],
    limit: int | str = 50,
    fleet_id: str | None = None,
    asset_id: str | None = None,
) -> HttpResponse:
    try:
        response_body = get_recent_readings_from_items(items, limit, fleet_id, asset_id)
    except (TypeError, ValueError):
        return invalid_query_response(limit, fleet_id, asset_id)
    return HttpResponse(200, response_body)


def invalid_query_response(
    limit: int | str,
    fleet_id: str | None,
    asset_id: str | None,
) -> HttpResponse:
    invalid_filter = (fleet_id is None) != (asset_id is None) or (
        fleet_id is not None and (not fleet_id.strip() or not asset_id.strip())
    )
    code = "invalid_train_filter" if invalid_filter else "invalid_limit"
    message = (
        "fleet_id and asset_id must be provided together and must not be empty."
        if invalid_filter
        else "The limit query parameter must be an integer."
    )
    return HttpResponse(400, {"error": {"code": code, "message": message}})
