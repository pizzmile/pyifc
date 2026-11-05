import json
import textwrap

import pytest

from pyifc.runtime.enforce import CapabilityError
from pyifc.runtime.local import LocalApp

APP = textwrap.dedent('''
    import json
    from pyifc import Application, Bucket, Topic, Queue, KeyValueStore

    app = Application("tiny")
    records = Bucket("records")
    events = Topic("events")
    jobs = Queue("jobs")
    index = KeyValueStore("index")

    @app.publish(events)
    @app.handler(method="POST", path="/records", id="ingest")
    def ingest(body, content_type):
        events.publish(body)
        return {"queued": True}

    @app.subscribe(events)
    @app.write(records)
    @app.publish(jobs)
    @app.worker(id="store")
    def store(message):
        record = json.loads(message)
        records.put(record["id"], message)
        jobs.publish(record["id"].encode())

    @app.subscribe(jobs)
    @app.write(index)
    @app.worker(id="indexer")
    def indexer(message):
        index.put(message.decode(), b"seen")

    @app.read(records)
    @app.read(index)
    @app.handler(method="GET", path="/records", id="query")
    def query(fleet_id=None):
        data, _ = records.get(fleet_id)
        return {"record": json.loads(data), "indexed": index.get(fleet_id).decode()}

    @app.read(records)
    @app.handler(method="GET", path="/bad", id="bad-reader")
    def bad_reader():
        records.put("oops", b"x")
        return {}

    @app.read(records)
    @app.handler(method="GET", path="/undeclared", id="undeclared")
    def undeclared():
        index.get("x")
        return {}
''')


@pytest.fixture
def app_file(tmp_path):
    path = tmp_path / "tinyapp.py"
    path.write_text(APP)
    return path


def test_post_deliver_get_round_trip(app_file, tmp_path):
    local = LocalApp(app_file, tmp_path / "state")

    status, body = local.call_http("POST", "/records", b'{"id":"r1","v":7}', {"content-type": "application/json"})
    assert status == 202
    assert json.loads(body) == {"queued": True}
    assert local.deliver() == 2  # the event to store, then the job it published to indexer

    status, body = local.call_http("GET", "/records?fleet_id=r1")
    assert status == 200
    assert json.loads(body) == {"indexed": "seen", "record": {"id": "r1", "v": 7}}


def test_bucket_objects_are_files_under_the_state_directory(app_file, tmp_path):
    state = tmp_path / "state"
    local = LocalApp(app_file, state)
    local.call_http("POST", "/records", b'{"id":"r1"}')
    local.deliver()

    assert (state / "buckets" / "records" / "r1").read_bytes() == b'{"id":"r1"}'


def test_state_persists_across_instances(app_file, tmp_path):
    state = tmp_path / "state"
    first = LocalApp(app_file, state)
    first.call_http("POST", "/records", b'{"id":"r1"}')
    first.deliver()

    second = LocalApp(app_file, state)
    status, _ = second.call_http("GET", "/records?fleet_id=r1")
    assert status == 200


def test_deliver_with_nothing_pending_returns_zero(app_file, tmp_path):
    assert LocalApp(app_file, tmp_path / "state").deliver() == 0


def test_unknown_route_is_404(app_file, tmp_path):
    status, _ = LocalApp(app_file, tmp_path / "state").call_http("GET", "/nothing")
    assert status == 404


def test_write_without_declared_capability_raises_when_enforced(app_file, tmp_path):
    local = LocalApp(app_file, tmp_path / "state", enforce=True)

    with pytest.raises(CapabilityError, match="bad-reader"):
        local.call_http("GET", "/bad")


def test_same_call_succeeds_when_enforcement_is_off(app_file, tmp_path):
    local = LocalApp(app_file, tmp_path / "state", enforce=False)

    status, _ = local.call_http("GET", "/bad")

    assert status == 200
    assert (tmp_path / "state" / "buckets" / "records" / "oops").read_bytes() == b"x"


def test_resource_the_component_never_declared_is_not_bound(app_file, tmp_path):
    local = LocalApp(app_file, tmp_path / "state")

    with pytest.raises(RuntimeError, match="No runtime adapter bound for index"):
        local.call_http("GET", "/undeclared")


def test_capability_error_in_a_worker_propagates_from_deliver(tmp_path):
    path = tmp_path / "workerapp.py"
    path.write_text(textwrap.dedent('''
        from pyifc import Application, Bucket, Queue
        app = Application("w")
        b = Bucket("b")
        q = Queue("q")

        @app.publish(q)
        @app.handler(method="POST", path="/go", id="go")
        def go(body, content_type):
            q.publish(b"m")
            return {}

        @app.subscribe(q)
        @app.read(b)
        @app.worker(id="w")
        def w(message):
            b.put("k", message)
    '''))
    local = LocalApp(path, tmp_path / "state")
    local.call_http("POST", "/go", b"")

    with pytest.raises(CapabilityError):
        local.deliver()


def test_async_components_run(tmp_path):
    path = tmp_path / "asyncapp.py"
    path.write_text(textwrap.dedent('''
        from pyifc import Application, Bucket
        app = Application("a")
        b = Bucket("b")

        @app.write(b)
        @app.handler(method="POST", path="/x", id="x")
        async def x(body, content_type):
            b.put("k", body)
            return {"ok": 1}
    '''))
    local = LocalApp(path, tmp_path / "state")

    status, _ = local.call_http("POST", "/x", b"v")

    assert status == 202
    assert (tmp_path / "state" / "buckets" / "b" / "k").read_bytes() == b"v"
