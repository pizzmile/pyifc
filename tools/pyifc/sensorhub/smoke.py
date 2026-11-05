"""Smoke script for `pyifc run`: one speed and one line-voltage reading through the local runner.

Defines ``main(app: LocalApp) -> int``. It posts the first sample of each kind from
``sensorhub/src/data``, delivers the topic messages, reads ``GET /readings`` and writes
``<state dir>/readings.json`` with the status of that last request.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[3] / "sensorhub" / "src" / "data"
HEADERS = {"content-type": "application/json"}


def sample(kind_directory: str, now: str) -> bytes:
    reading = json.loads((SAMPLES / kind_directory / "reading-001.json").read_text())
    reading["timestamp"] = now
    return json.dumps(reading).encode("utf-8")


def main(app) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for directory in ("speed", "line-voltage"):
        status, body = app.call_http("POST", "/readings", sample(directory, now), HEADERS)
        print(f"POST {directory}: {status}")
        if status != 202:
            print(body.decode("utf-8"))
            return 1
    print(f"delivered: {app.deliver()}")
    status, body = app.call_http("GET", "/readings?limit=100")
    print(f"GET /readings: {status}")
    (app.state_dir / "readings.json").write_text(json.dumps({"http_status": status}))
    if status != 200:
        return 1
    count = len(json.loads(body)["readings"])
    print(f"readings: {count}")
    return 0 if count >= 2 else 1
