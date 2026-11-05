import json
import sys
import textwrap

import pytest

from pyifc.runtime import aws_adapters as aws_runtime
from pyifc.runtime import entry

APP = textwrap.dedent('''
    from pyifc import Application, Bucket, Queue, Topic, Secret

    app = Application("entrytest")
    raw = Bucket("raw")
    other = Bucket("other")
    jobs = Queue("jobs")
    news = Topic("news")
    token = Secret("token")

    @app.write(raw)
    @app.publish(jobs)
    @app.handler(method="POST", path="/in", id="ingestion")
    def ingest(body, content_type):
        raw.put("k", body)
        jobs.publish(b"j")
        return {"ok": True}

    @app.read(raw)
    @app.handler(method="GET", path="/out", id="query")
    def query(fleet_id=None):
        data, _ = raw.get("k")
        return {"fleet": fleet_id, "data": data.decode()}

    @app.subscribe(jobs)
    @app.write(other)
    @app.worker(id="consumer")
    def consume(message):
        other.put("m", message)

    @app.subscribe(news)
    @app.worker(id="newsreader")
    def read_news(message):
        other.put("n", message)
''')


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def put(self, key, body, content_type="application/json"):
        self.objects[key] = (body, content_type)

    def get(self, key):
        return self.objects[key]


class FakeQueue:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    (tmp_path / "entry_app.py").write_text(APP)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYIFC_APP_MODULE", "entry_app")
    yield
    sys.modules.pop("entry_app", None)


@pytest.fixture
def bound(monkeypatch):
    requested = []
    stores = {"raw": FakeBucket(), "other": FakeBucket(), "jobs": FakeQueue()}

    def fake_bind(resources):
        requested.append(sorted(resource.id for resource in resources))
        return {resource.id: stores[resource.id] for resource in resources}

    monkeypatch.setattr(aws_runtime, "bind_from_environment", fake_bind)
    return requested, stores


def test_handler_binds_only_the_resources_the_component_declared(app_env, bound, monkeypatch):
    requested, stores = bound
    monkeypatch.setenv("PYIFC_COMPONENT", "ingestion")

    response = entry.handler({"body": '{"a":1}', "headers": {}}, None)

    assert requested == [["jobs", "raw"]]
    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"ok": True}
    assert stores["raw"].objects["k"][0] == b'{"a":1}'
    assert stores["jobs"].messages == [b"j"]


def test_get_component_is_dispatched_to_handle_get(app_env, bound, monkeypatch):
    requested, stores = bound
    stores["raw"].put("k", b"hello")
    monkeypatch.setenv("PYIFC_COMPONENT", "query")

    response = entry.handler({"queryStringParameters": {"fleet_id": "f1"}}, None)

    assert requested == [["raw"]]
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"data": "hello", "fleet": "f1"}


def test_queue_worker_is_dispatched_to_handle_worker_event(app_env, bound, monkeypatch):
    requested, stores = bound
    monkeypatch.setenv("PYIFC_COMPONENT", "consumer")

    entry.handler({"Records": [{"body": "payload"}]}, None)

    assert requested == [["jobs", "other"]]
    assert stores["other"].objects["m"][0] == b"payload"


def test_unknown_component_raises_runtime_error_naming_it(app_env, bound, monkeypatch):
    monkeypatch.setenv("PYIFC_COMPONENT", "nope")

    with pytest.raises(RuntimeError, match="nope"):
        entry.handler({}, None)


def test_missing_environment_variable_is_reported(monkeypatch):
    monkeypatch.delenv("PYIFC_COMPONENT", raising=False)
    monkeypatch.setenv("PYIFC_APP_MODULE", "whatever")

    with pytest.raises(RuntimeError, match="PYIFC_COMPONENT"):
        entry.handler({}, None)


def test_app_module_can_be_a_file_path(tmp_path, bound, monkeypatch):
    path = tmp_path / "by_path.py"
    path.write_text(APP)
    monkeypatch.setenv("PYIFC_APP_MODULE", str(path))
    monkeypatch.setenv("PYIFC_COMPONENT", "ingestion")

    assert entry.handler({"body": "x"}, None)["statusCode"] == 202


def test_aws_target_wrappers_are_unwrapped(tmp_path, bound, monkeypatch):
    # With PYIFC_TARGET=aws the decorators return wrappers; entry calls the plain function.
    path = tmp_path / "aws_target.py"
    path.write_text(APP)
    monkeypatch.setenv("PYIFC_TARGET", "aws")
    monkeypatch.setenv("PYIFC_APP_MODULE", str(path))
    monkeypatch.setenv("PYIFC_COMPONENT", "ingestion")

    assert entry.handler({"body": "x"}, None)["statusCode"] == 202
    assert bound[0] == [["jobs", "raw"]]
