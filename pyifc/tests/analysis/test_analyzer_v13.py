"""Graph schema 1.3 and the analyzer checks added with it."""
from __future__ import annotations

import pytest

from pyifc.analysis import analyze_source


def codes(diagnostics) -> list[str]:
    return [item.code for item in diagnostics]


def edge(result, component, resource, operation) -> dict:
    (found,) = [
        item
        for item in result.specification["capabilities"]
        if (item["component_id"], item["resource_id"], item["operation"]) == (component, resource, operation)
    ]
    return found


def test_schema_version_is_13() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "b = Bucket('b')\n"
        "@app.handler(method='GET', path='/b', id='h')\n"
        "@app.read(b)\n"
        "def h():\n    b.get('k')\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert result.specification["schema_version"] == "1.3"


def test_unused_capability_keeps_declared_provenance() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "b = Bucket('b')\n"
        "@app.handler(method='POST', path='/b', id='h')\n"
        "@app.write(b)\n"
        "def h():\n    return None\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert codes(result.warnings) == ["W_UNUSED_CAPABILITY"]
    assert edge(result, "h", "b", "write")["provenance"] == "declared"


def test_unused_publish_warns() -> None:
    source = (
        "from pyifc import Application, Topic\n"
        "app = Application('demo')\n"
        "t = Topic('t')\n"
        "@app.handler(method='POST', path='/t', id='h')\n"
        "@app.publish(t)\n"
        "def h():\n    return None\n"
        "@app.worker(id='w')\n"
        "@app.subscribe(t)\n"
        "def w(message):\n    return None\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert codes(result.warnings) == ["W_UNUSED_CAPABILITY"]
    assert edge(result, "h", "t", "publish")["provenance"] == "declared"


def test_subscribe_on_own_trigger_is_both() -> None:
    source = (
        "from pyifc import Application, Topic\n"
        "app = Application('demo')\n"
        "t = Topic('t')\n"
        "@app.handler(method='POST', path='/t', id='h')\n"
        "@app.publish(t)\n"
        "def h():\n    t.publish(b'x')\n"
        "@app.worker(id='w')\n"
        "@app.subscribe(t)\n"
        "def w(message):\n    return None\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert result.warnings == ()
    assert edge(result, "w", "t", "subscribe")["provenance"] == "both"
    assert edge(result, "h", "t", "publish")["provenance"] == "both"


def test_unused_resource_warns_and_stays() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "x = Bucket('x')\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert codes(result.warnings) == ["W_UNUSED_RESOURCE"]
    assert [item["id"] for item in result.specification["resources"]] == ["x"]


def test_schedule_used_as_trigger_is_not_unused() -> None:
    source = (
        "from pyifc import Application, Schedule\n"
        "app = Application('demo')\n"
        "tick = Schedule('tick', cron='rate(1 minute)')\n"
        "@app.scheduled(tick, id='job')\n"
        "def job():\n    return None\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    assert "W_UNUSED_RESOURCE" not in codes(result.warnings)


def test_topic_without_publisher_is_error() -> None:
    topic = (
        "from pyifc import Application, Topic\n"
        "app = Application('demo')\n"
        "t = Topic('t')\n"
        "@app.worker(id='w')\n"
        "@app.subscribe(t)\n"
        "def w(message):\n    t.receive()\n"
    )
    result = analyze_source(topic)
    assert result.specification is None
    assert "E_TOPIC_MISSING_PUBLISHER" in codes(result.errors)
    assert "E_QUEUE_MISSING_PUBLISHER" not in codes(result.errors)

    queue = topic.replace("Topic", "Queue")
    result = analyze_source(queue)
    assert result.specification is None
    assert "E_QUEUE_MISSING_PUBLISHER" in codes(result.errors)
    assert "E_TOPIC_MISSING_PUBLISHER" not in codes(result.errors)


def test_topic_publisher_without_subscriber_names_the_kind() -> None:
    source = (
        "from pyifc import Application, Topic\n"
        "app = Application('demo')\n"
        "t = Topic('t')\n"
        "@app.handler(method='POST', path='/t', id='h')\n"
        "@app.publish(t)\n"
        "def h():\n    t.publish(b'x')\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    (warning,) = [item for item in result.warnings if item.code == "W_QUEUE_NO_SUBSCRIBER"]
    assert "Topic" in warning.message
    assert "Queue" not in warning.message


def test_name_collisions() -> None:
    terraform = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "first = Bucket('a-b')\n"
        "second = Bucket('a_b')\n"
    )
    result = analyze_source(terraform)
    assert result.specification is None
    assert codes(result.errors) == ["E_NAME_COLLISION"]
    assert result.errors[0].line == 4

    physical = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "first = Bucket('a-b')\n"
        "second = Bucket('demo-a-b')\n"
    )
    result = analyze_source(physical)
    assert result.specification is None
    assert codes(result.errors) == ["E_NAME_COLLISION"]

    distinct = physical.replace("demo-a-b", "demo-c")
    assert analyze_source(distinct).errors == ()


def test_handler_with_subscribe_is_error() -> None:
    source = (
        "from pyifc import Application, Queue\n"
        "app = Application('demo')\n"
        "q = Queue('q')\n"
        "@app.handler(method='POST', path='/q', id='h')\n"
        "@app.publish(q)\n"
        "@app.subscribe(q)\n"
        "def h():\n    q.publish(b'x')\n    q.receive()\n"
    )
    result = analyze_source(source)
    assert result.specification is None
    assert "E_HANDLER_SUBSCRIBE" in codes(result.errors)


def test_stacked_handler_and_worker_is_error() -> None:
    source = (
        "from pyifc import Application, Queue\n"
        "app = Application('demo')\n"
        "q = Queue('q')\n"
        "@app.handler(method='POST', path='/q', id='h')\n"
        "@app.worker(id='w')\n"
        "@app.subscribe(q)\n"
        "def h():\n    q.receive()\n"
    )
    result = analyze_source(source)
    assert result.specification is None
    assert "E_STACKED_COMPONENT_DECORATORS" in codes(result.errors)


def test_component_without_id_warns() -> None:
    template = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "b = Bucket('b')\n"
        "@app.handler(method='GET', path='/b'{id})\n"
        "@app.read(b)\n"
        "def h():\n    b.get('k')\n"
    )
    without = analyze_source(template.format(id=""), module_name="mod")
    assert without.specification is not None
    assert codes(without.warnings) == ["W_COMPONENT_DEFAULT_ID"]
    assert "mod.h" in without.warnings[0].message

    with_id = analyze_source(template.format(id=", id='h'"), module_name="mod")
    assert with_id.specification is not None
    assert with_id.warnings == ()


def test_was_argument() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "b = Bucket('new', was='old')\n"
        "plain = Bucket('plain')\n"
    )
    result = analyze_source(source)
    assert result.specification is not None
    by_id = {item["id"]: item for item in result.specification["resources"]}
    assert by_id["new"]["was"] == "old"
    assert "was" not in by_id["plain"]


@pytest.mark.parametrize("was", ["name", "''", "1", "other"])
def test_was_must_be_a_literal_other_than_own_id(was: str) -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "other = 'x'\n"
        "app = Application('demo')\n"
        f"b = Bucket('new', was={was if was != 'name' else repr('new')})\n"
    )
    result = analyze_source(source)
    assert result.specification is None
    assert "E_RESOURCE_WAS" in codes(result.errors)


def _ingest_query(padding: str = "") -> str:
    return (
        "from pyifc import Application, Bucket\n"
        f"{padding}"
        "app = Application('demo')\n"
        "data = Bucket('data')\n"
        "@app.handler(method='POST', path='/in', id='ingestion')\n"
        "@app.write(data)\n"
        "def ingestion():\n    data.put('k', b'v')\n"
        f"{padding}"
        "@app.handler(method='GET', path='/out', id='query')\n"
        "@app.read(data)\n"
        "@app.delete(data)\n"
        "def query():\n    data.get('k')\n"
    )


def test_use_signature_ignores_source_positions() -> None:
    plain = analyze_source(_ingest_query())
    padded = analyze_source(_ingest_query("\n\n\n"))
    assert plain.specification is not None and padded.specification is not None
    (resource,) = plain.specification["resources"]
    # the unused delete is declared only: it is not part of the signature
    assert resource["use_signature"] == ["ingestion>write", "query>read"]
    assert padded.specification["resources"][0]["use_signature"] == resource["use_signature"]
    assert padded.specification["capabilities"] != plain.specification["capabilities"]


def test_use_signature_is_empty_for_unused_resource_and_sorted() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "x = Bucket('x')\n"
    )
    result = analyze_source(source)
    assert result.specification["resources"][0]["use_signature"] == []


def test_scheduled_job_with_subscribe_is_error() -> None:
    source = (
        "from pyifc import Application, Queue, Schedule\n"
        "app = Application('demo')\n"
        "tick = Schedule('tick', cron='rate(1 minute)')\n"
        "q = Queue('q')\n"
        "@app.scheduled(tick, id='job')\n"
        "@app.publish(q)\n"
        "@app.subscribe(q)\n"
        "def job():\n    q.publish(b'x')\n    q.receive()\n"
    )
    result = analyze_source(source)
    assert result.specification is None
    assert "E_HANDLER_SUBSCRIBE" in codes(result.errors)
    message = next(item for item in result.errors if item.code == "E_HANDLER_SUBSCRIBE").message
    assert "cannot subscribe" in message


def test_default_id_warning_is_right_for_non_literal_id() -> None:
    source = (
        "from pyifc import Application, Bucket\n"
        "app = Application('demo')\n"
        "b = Bucket('b')\n"
        "name = 'h'\n"
        "@app.handler(method='GET', path='/b', id=name)\n"
        "@app.read(b)\n"
        "def h():\n    b.get('k')\n"
    )
    result = analyze_source(source, module_name="mod")
    (warning,) = [item for item in result.warnings if item.code == "W_COMPONENT_DEFAULT_ID"]
    assert "no literal id=" in warning.message
    assert "no explicit id=" not in warning.message
