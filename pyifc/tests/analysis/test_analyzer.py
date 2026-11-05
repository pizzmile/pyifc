from pathlib import Path

from pyifc.analysis import analyze_file, analyze_source

THUMBNAILER = Path(__file__).parents[2] / "examples" / "thumbnailer" / "app.py"


def test_thumbnailer_produces_checked_spec_without_importing_source() -> None:
    result = analyze_file(THUMBNAILER)

    assert result.errors == ()
    assert result.specification is not None

    specification = result.specification
    assert specification["name"] == "thumbnailer"
    assert specification["schema_version"] == "1.3"
    assert [resource["id"] for resource in specification["resources"]] == [
        "thumbnail-jobs",
        "thumbnails",
        "uploads",
    ]

    capabilities = {
        (
            capability["component_id"],
            capability["resource_id"],
            capability["operation"],
            capability["provenance"],
        )
        for capability in specification["capabilities"]
    }
    assert capabilities == {
        ("upload-handler", "uploads", "write", "both"),
        ("upload-handler", "thumbnail-jobs", "publish", "both"),
        ("thumbnail-worker", "thumbnail-jobs", "subscribe", "both"),
        ("thumbnail-worker", "uploads", "read", "both"),
        ("thumbnail-worker", "thumbnails", "write", "both"),
    }

    assert not any(
        name == "examples.thumbnailer.app"
        for name in __import__("sys").modules
    )


def test_thumbnailer_analysis_is_deterministic() -> None:
    first = analyze_file(THUMBNAILER)
    second = analyze_file(THUMBNAILER)

    assert first.to_json() == second.to_json()


def test_missing_application_declaration_is_an_error() -> None:
    result = analyze_source("from pyifc import Bucket\nitems = Bucket('items')\n")

    assert result.specification is None
    assert [item.code for item in result.errors] == ["E_APPLICATION_DECLARATION"]


def test_resource_alias_is_rejected() -> None:
    source = '''
from pyifc import Application, Bucket
app = Application("alias-test")
items = Bucket("items")
@app.handler(method="GET", path="/items")
@app.read(items)
def list_items():
    alias = items
    alias.get("one")
'''
    result = analyze_source(source)

    assert result.specification is None
    assert "E_UNSUPPORTED_ALIAS" in [item.code for item in result.errors]


def test_undeclared_resource_use_is_an_error() -> None:
    source = '''
from pyifc import Application, Bucket
app = Application("undeclared-test")
items = Bucket("items")
@app.handler(method="GET", path="/items")
def list_items():
    items.get("one")
'''
    result = analyze_source(source)

    assert result.specification is None
    assert "E_UNDECLARED_USE" in [item.code for item in result.errors]


def test_worker_requires_queue_subscription() -> None:
    source = '''
from pyifc import Application
app = Application("worker-test")
@app.worker()
def orphan_worker():
    pass
'''
    result = analyze_source(source)

    assert result.specification is None
    assert "E_WORKER_TRIGGER" in [item.code for item in result.errors]


def test_explicit_handler_id_applies_to_capabilities_independent_of_order() -> None:
    from pyifc import Application, Bucket

    app = Application("decorator-order")
    items = Bucket("items")

    @app.handler(method="GET", path="/items", id="items-handler")
    @app.read(items)
    def list_items():
        return None

    spec = app.to_spec()
    assert spec["capabilities"][0]["component_id"] == "items-handler"


def test_declared_but_unused_capability_is_a_warning() -> None:
    source = '''
from pyifc import Application, Bucket
app = Application("unused-test")
items = Bucket("items")
@app.handler(method="GET", path="/items", id="list-items")
@app.read(items)
def list_items():
    return None
'''
    result = analyze_source(source)

    assert result.specification is not None
    assert [item.code for item in result.warnings] == ["W_UNUSED_CAPABILITY"]


def test_queue_publisher_without_subscriber_is_a_warning() -> None:
    source = '''
from pyifc import Application, Queue
app = Application("queue-warning")
jobs = Queue("jobs")
@app.handler(method="POST", path="/jobs")
@app.publish(jobs)
def enqueue():
    jobs.publish(b"job")
'''
    result = analyze_source(source)

    assert result.specification is not None
    assert "W_QUEUE_NO_SUBSCRIBER" in [item.code for item in result.warnings]


def test_missing_queue_publisher_is_an_error() -> None:
    source = "from pyifc import Application, Queue\napp = Application('q')\njobs = Queue('jobs')\n@app.worker()\n@app.subscribe(jobs)\ndef consume():\n    jobs.receive()\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_QUEUE_MISSING_PUBLISHER" in [item.code for item in result.errors]


def test_point_to_point_queue_rejects_multiple_subscribers() -> None:
    source = "from pyifc import Application, Queue\napp = Application('q')\njobs = Queue('jobs')\n@app.handler(method='POST', path='/jobs')\n@app.publish(jobs)\ndef enqueue():\n    jobs.publish(b'job')\n@app.worker(id='one')\n@app.subscribe(jobs)\ndef one():\n    jobs.receive()\n@app.worker(id='two')\n@app.subscribe(jobs)\ndef two():\n    jobs.receive()\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_QUEUE_MULTIPLE_SUBSCRIBERS" in [item.code for item in result.errors]


def test_duplicate_component_id_is_an_error() -> None:
    source = "from pyifc import Application\napp = Application('duplicate')\n@app.handler(method='GET', path='/one', id='same')\ndef first(): pass\n@app.handler(method='GET', path='/two', id='same')\ndef second(): pass\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_DUPLICATE_COMPONENT_ID" in [item.code for item in result.errors]


def test_unsupported_application_decorator_is_an_error() -> None:
    source = "from pyifc import Application\napp = Application('unsupported')\n@app.schedule()\ndef scheduled(): pass\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_DECORATOR" in [item.code for item in result.errors]


def test_capability_requires_handler_or_worker() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('orphan')\nitems = Bucket('items')\n@app.read(items)\ndef orphan(): pass\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_COMPONENT_DECLARATION" in [item.code for item in result.errors]


def test_resource_passed_to_helper_is_rejected() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('passed')\nitems = Bucket('items')\n@app.handler(method='GET', path='/items')\n@app.read(items)\ndef read_items():\n    helper(items)\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_INDIRECT_USE" in [item.code for item in result.errors]


def test_resource_method_saved_as_value_is_rejected() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('method-alias')\nitems = Bucket('items')\n@app.handler(method='GET', path='/items')\n@app.read(items)\ndef read_items():\n    getter = items.get\n    getter('one')\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_INDIRECT_USE" in [item.code for item in result.errors]


def test_aliased_pyifc_import_is_rejected() -> None:
    source = "from pyifc import Application as IfcApp\napp = IfcApp('alias-import')\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_IMPORT" in [item.code for item in result.errors]


def test_nested_resource_constructor_is_rejected() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('nested-resource')\nif True:\n    items = Bucket('items')\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_RESOURCE_DECLARATION" in [item.code for item in result.errors]


def test_resource_access_in_undecorated_helper_is_rejected() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('helper')\nitems = Bucket('items')\n@app.handler(method='GET', path='/items')\ndef read_items():\n    return helper()\ndef helper():\n    return items.get('one')\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_HELPER_RESOURCE_USE" in [item.code for item in result.errors]


def test_resource_access_in_nested_function_is_rejected() -> None:
    source = "from pyifc import Application, Bucket\napp = Application('nested-helper')\nitems = Bucket('items')\n@app.handler(method='GET', path='/items')\n@app.read(items)\ndef read_items():\n    def helper():\n        return items.get('one')\n    return helper()\n"
    result = analyze_source(source)
    assert result.specification is None
    assert "E_UNSUPPORTED_NESTED_RESOURCE_USE" in [item.code for item in result.errors]


def test_full_resource_vocabulary_and_scheduled_component() -> None:
    source = '''
from pyifc import Application, KeyValueStore, Queue, Schedule, Secret, Topic
app = Application("full-vocabulary")
cache = KeyValueStore("cache")
events = Topic("events")
jobs = Queue("jobs")
nightly = Schedule("nightly", cron="0 2 * * *")
api_key = Secret("api-key")
@app.handler(method="POST", path="/events")
@app.publish(events)
def emit():
    events.publish(b"event")
@app.worker(id="consumer-one")
@app.subscribe(events)
def consumer_one():
    events.receive()
@app.worker(id="consumer-two")
@app.subscribe(events)
def consumer_two():
    events.receive()
@app.scheduled(nightly)
@app.read(api_key)
@app.read(cache)
@app.write(cache)
def rotate_cache():
    cache.put("last-run", b"now")
    cache.get("last-run")
'''
    result = analyze_source(source, filename="full_vocabulary.py")

    assert result.errors == ()
    assert result.specification is not None
    spec = result.specification
    assert {resource["kind"] for resource in spec["resources"]} == {
        "key_value_store",
        "queue",
        "schedule",
        "secret",
        "topic",
    }
    assert next(item for item in spec["resources"] if item["id"] == "nightly")["properties"] == {
        "cron": "0 2 * * *"
    }
    scheduled = next(item for item in spec["components"] if item["id"].endswith(".rotate_cache"))
    assert scheduled["trigger"] == "schedule:nightly"
    assert len([item for item in spec["components"] if item["trigger"] == "topic:events"]) == 2


def test_preview_and_analyzer_share_graph_field_shapes() -> None:
    from pyifc import Application, Bucket

    app = Application("shape")
    objects = Bucket("objects")

    @app.handler(method="GET", path="/objects", id="query")
    @app.list(objects)
    def query():
        objects.list()

    preview = app.to_spec()
    analyzed = analyze_source(
        "from pyifc import Application, Bucket\n"
        "app = Application('shape')\n"
        "objects = Bucket('objects')\n"
        "@app.handler(method='GET', path='/objects', id='query')\n"
        "@app.list(objects)\n"
        "def query():\n    objects.list()\n",
        filename="app.py",
    ).specification

    assert analyzed is not None
    assert preview.keys() == analyzed.keys()
    assert preview["schema_version"] == analyzed["schema_version"] == "1.3"
    assert preview["components"][0].keys() == analyzed["components"][0].keys()
    assert preview["resources"][0].keys() == analyzed["resources"][0].keys()
    assert preview["capabilities"][0].keys() == analyzed["capabilities"][0].keys()


def test_analyze_file_uses_root_relative_paths_and_module_names() -> None:
    result = analyze_file(THUMBNAILER, source_root=THUMBNAILER.parents[2])

    assert result.specification is not None
    assert result.specification["components"][0]["source"]["file"] == "examples/thumbnailer/app.py"
    assert result.specification["components"][0]["callable_ref"].startswith("examples.thumbnailer.app:")


def test_secret_write_capability_is_rejected() -> None:
    source = "from pyifc import Application, Secret\napp = Application('secret-write')\ncredential = Secret('credential')\n@app.handler(method='POST', path='/secret')\n@app.write(credential)\ndef change_secret():\n    credential.get()\n"
    result = analyze_source(source)

    assert result.specification is None
    assert "E_INVALID_CAPABILITY" in [item.code for item in result.errors]
