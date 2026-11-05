import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pyifc import analyze_file
from pyifc.runtime.local import LocalApp

PORT = Path(__file__).resolve().parents[1]
APP = PORT / "app.py"

EXPECTED_USES = {
    ("ingestion", "sensorhub-staging", "write"),
    ("ingestion", "sensorhub-speed", "publish"),
    ("ingestion", "sensorhub-line-voltage", "publish"),
    ("speed-parser", "sensorhub-speed", "subscribe"),
    ("speed-parser", "sensorhub-staging", "read"),
    ("speed-parser", "sensorhub-results", "write"),
    ("line-voltage-parser", "sensorhub-line-voltage", "subscribe"),
    ("line-voltage-parser", "sensorhub-staging", "read"),
    ("line-voltage-parser", "sensorhub-results", "write"),
    ("query", "sensorhub-results", "read"),
}


@pytest.fixture(autouse=True)
def port_on_path():
    sys.path.insert(0, str(PORT))
    yield
    sys.path.remove(str(PORT))
    for name in [n for n in sys.modules if n == "sensorhub_logic" or n.startswith("sensorhub_logic.")]:
        del sys.modules[name]


def test_the_port_analyzes_without_errors():
    result = analyze_file(APP)

    assert result.errors == ()
    assert result.specification["name"] == "sensorhub"


def test_the_graph_has_four_resources_and_the_ten_uses():
    specification = analyze_file(APP).specification

    assert {item["id"] for item in specification["resources"]} == {
        "sensorhub-staging",
        "sensorhub-results",
        "sensorhub-speed",
        "sensorhub-line-voltage",
    }
    assert {item["kind"] for item in specification["resources"]} == {"object_store", "topic"}
    assert {item["id"] for item in specification["components"]} == {
        "ingestion", "speed-parser", "line-voltage-parser", "query",
    }
    # Listing a bucket is its own capability in the graph; the common form folds it into read.
    uses = {
        (item["component_id"], item["resource_id"], "read" if item["operation"] == "list" else item["operation"])
        for item in specification["capabilities"]
    }
    assert uses == EXPECTED_USES


def test_smoke_script_posts_two_readings_and_lists_them(tmp_path):
    spec = importlib.util.spec_from_file_location("port_smoke", PORT / "smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    local = LocalApp(APP, tmp_path / "state", enforce=True)

    assert smoke.main(local) == 0

    status, body = local.call_http("GET", "/readings?limit=100")
    assert status == 200
    assert len(json.loads(body)["readings"]) == 2
    assert json.loads((tmp_path / "state" / "readings.json").read_text()) == {"http_status": 200}
