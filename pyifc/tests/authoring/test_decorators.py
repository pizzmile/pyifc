from pyifc import Application, Bucket, KeyValueStore, Queue, Schedule, Secret, Topic
from pyifc.authoring.registry import DeclarationError
from pyifc.core.binding import bind


def test_decorators_produce_application_specification() -> None:
    app = Application("test-application")

    uploads = Bucket("uploads")
    jobs = Queue("jobs")

    @app.handler(method="post", path="/uploads")
    @app.write(uploads)
    @app.publish(jobs)
    def upload_handler(request):
        return request

    spec = app.to_spec()

    assert spec["name"] == "test-application"
    assert spec["schema_version"] == "1.3"

    assert spec["resources"] == [
        {
            "id": "jobs",
            "kind": "queue",
            "properties": {},
            "stateful": False,
            "use_signature": [],
        },
        {
            "id": "uploads",
            "kind": "object_store",
            "properties": {},
            "stateful": True,
            "use_signature": [],
        },
    ]

    assert len(spec["components"]) == 1

    component = spec["components"][0]

    assert component["id"].endswith(".upload_handler")
    assert component["kind"] == "http_handler"
    assert component["properties"] == {
        "method": "POST",
        "path": "/uploads",
    }

    capabilities = {
        (
            capability["component_id"],
            capability["resource_id"],
            capability["operation"],
        )
        for capability in spec["capabilities"]
    }

    assert capabilities == {
        (component["id"], "uploads", "write"),
        (component["id"], "jobs", "publish"),
    }


def test_worker_registration() -> None:
    app = Application("worker-application")

    uploads = Bucket("uploads")
    jobs = Queue("jobs")

    @app.worker()
    @app.subscribe(jobs)
    @app.read(uploads)
    def process_job(message):
        return message

    spec = app.to_spec()

    assert spec["components"][0]["id"].endswith(".process_job")
    assert spec["components"][0]["kind"] == "worker"

    capabilities = {
        capability["operation"]
        for capability in spec["capabilities"]
    }

    assert capabilities == {
        "read",
        "subscribe",
    }


def test_aws_worker_dispatch_binds_only_declared_resources(monkeypatch) -> None:
    import pyifc.runtime.aws_adapters as aws_runtime

    monkeypatch.setenv("PYIFC_TARGET", "aws")
    app = Application("aws-worker")
    staging = Bucket("staging")
    jobs = Queue("jobs")
    resource_ids = []
    received = []

    class Store:
        def get(self, key):
            return b"staged", "application/json"

    def bound_adapters(resources):
        resource_ids.extend(resource.id for resource in resources)
        return {"staging": Store()}

    def dispatch(function, event, adapters):
        assert event == {"Records": [{"body": "job"}]}
        with bind(adapters):
            function(b"job")
        return "dispatched"

    monkeypatch.setattr(aws_runtime, "bind_from_environment", bound_adapters)
    monkeypatch.setattr(aws_runtime, "handle_sqs", dispatch)

    @app.event_handler(id="worker")
    @app.subscribe(jobs)
    @app.read(staging)
    def consume(message):
        received.append((message, staging.get("raw/reading.json")))

    result = consume({"Records": [{"body": "job"}]}, None)

    assert result == "dispatched"
    assert set(resource_ids) == {"staging", "jobs"}
    assert received == [(b"job", (b"staged", "application/json"))]


def test_aws_get_handler_uses_get_adapter_with_declared_resources(monkeypatch) -> None:
    import pyifc.runtime.aws_adapters as aws_runtime

    monkeypatch.setenv("PYIFC_TARGET", "aws")
    app = Application("aws-get")
    results = Bucket("results")
    bound_resource_ids = []
    handled = []

    def bound_adapters(resources):
        bound_resource_ids.extend(resource.id for resource in resources)
        return {"results": object()}

    def dispatch(function, event, adapters):
        handled.append((function, event, adapters))
        return {"statusCode": 200}

    monkeypatch.setattr(aws_runtime, "bind_from_environment", bound_adapters)
    monkeypatch.setattr(aws_runtime, "handle_get", dispatch)

    @app.handler(method="GET", path="/readings", id="query")
    @app.list(results)
    @app.read(results)
    def query():
        return {"readings": []}

    response = query({"queryStringParameters": {"limit": "5"}}, None)

    assert response == {"statusCode": 200}
    assert bound_resource_ids == ["results"]
    assert handled[0][0] is query.__wrapped__
    assert handled[0][1] == {"queryStringParameters": {"limit": "5"}}
    assert set(handled[0][2]) == {"results"}


def test_duplicate_capabilities_are_deduplicated() -> None:
    app = Application("test-application")
    uploads = Bucket("uploads")

    @app.worker()
    @app.read(uploads)
    @app.read(uploads)
    def process():
        pass

    spec = app.to_spec()

    assert len(spec["capabilities"]) == 1


def test_explicit_worker_id_applies_independent_of_decorator_order() -> None:
    app = Application("worker-id")
    jobs = Queue("jobs")

    @app.worker(id="jobs-worker")
    @app.subscribe(jobs)
    def process_job():
        return None

    assert app.to_spec()["capabilities"][0]["component_id"] == "jobs-worker"


def test_duplicate_component_id_cannot_merge_different_callables() -> None:
    app = Application("duplicate-id")

    @app.handler(method="GET", path="/first", id="same")
    def first():
        return None

    try:
        @app.handler(method="GET", path="/second", id="same")
        def second():
            return None
    except DeclarationError:
        pass
    else:
        raise AssertionError("Duplicate component IDs must be rejected.")


def test_list_and_delete_are_public_capability_decorators() -> None:
    app = Application("bucket-operations")
    objects = Bucket("objects")

    @app.handler(method="GET", path="/objects")
    @app.list(objects)
    @app.read(objects)
    def query_objects():
        return None

    @app.handler(method="DELETE", path="/objects/{key}")
    @app.delete(objects)
    def delete_object():
        return None

    capabilities = {
        (item["component_id"], item["resource_id"], item["operation"])
        for item in app.to_spec()["capabilities"]
    }
    query_component = next(
        item["id"]
        for item in app.to_spec()["components"]
        if item["properties"].get("method") == "GET"
    )
    delete_component = next(
        item["id"]
        for item in app.to_spec()["components"]
        if item["properties"].get("method") == "DELETE"
    )
    assert capabilities == {
        (query_component, "objects", "list"),
        (query_component, "objects", "read"),
        (delete_component, "objects", "delete"),
    }


def test_pdf_resource_kinds_can_be_declared() -> None:
    app = Application("vocabulary")
    cache = KeyValueStore("cache")
    events = Topic("events")
    credential = Secret("credential")
    nightly = Schedule("nightly", cron="0 0 * * *")

    @app.event_handler(id="event-consumer")
    @app.subscribe(events)
    def consume_event():
        events.receive()

    @app.scheduled(nightly, id="nightly-job")
    @app.read(credential)
    @app.write(cache)
    def refresh_cache():
        credential.get()
        cache.put("last-run", b"now")

    resources = {item["id"]: item for item in app.to_spec()["resources"]}
    assert resources["cache"]["kind"] == "key_value_store"
    assert resources["events"]["kind"] == "topic"
    assert resources["credential"]["kind"] == "secret"
    assert resources["nightly"]["kind"] == "schedule"

    components = {item["id"]: item for item in app.to_spec()["components"]}
    assert components["event-consumer"]["kind"] == "worker"
    assert components["nightly-job"]["trigger"] == "schedule:nightly"


def test_topic_allows_multiple_event_subscribers() -> None:
    app = Application("topic-fanout")
    events = Topic("events")

    @app.handler(method="POST", path="/events")
    @app.publish(events)
    def publish_event():
        events.publish(b"event")

    @app.event_handler(id="subscriber-one")
    @app.subscribe(events)
    def first_subscriber():
        events.receive()

    @app.event_handler(id="subscriber-two")
    @app.subscribe(events)
    def second_subscriber():
        events.receive()

    spec = app.to_spec()
    assert next(item for item in spec["resources"] if item["id"] == "events")["kind"] == "topic"
    assert len(
        [item for item in spec["capabilities"] if item["operation"] == "subscribe"]
    ) == 2