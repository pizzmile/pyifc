from __future__ import annotations

import json

import pytest

from pyifc.identity import (
    IdentityResult,
    LockEntry,
    LockError,
    load_lock,
    resolve_identity,
    write_lock,
)

STATEFUL = {"object_store", "key_value_store", "secret"}


def res(rid, kind="object_store", props=None, sig=("a",), was=None):
    item = {
        "id": rid,
        "kind": kind,
        "properties": {} if props is None else props,
        "stateful": kind in STATEFUL,
        "use_signature": list(sig),
    }
    if was is not None:
        item["was"] = was
    return item


def spec(*resources):
    return {"resources": list(resources)}


def entry(kind="object_store", physical="p", sig=("a",), props=None):
    return LockEntry(kind, physical, tuple(sig), {} if props is None else props)


def phys(rid):
    return f"phys-{rid}"


def run(spec_, lock, **kw):
    return resolve_identity(spec_, lock, physical_name_for=phys, **kw)


def codes(result):
    return sorted(d.code for d in result.diagnostics)


def test_first_generation_requires_init():
    graph = spec(res("a"), res("t", "topic"))
    result = run(graph, None)
    assert [d.code for d in result.errors] == ["E_LOCK_MISSING"]
    assert result.errors[0].file == "pyifc.lock.json"
    result = run(graph, None, init=True)
    assert result.errors == ()
    assert result.physical_names == {"a": "phys-a", "t": "phys-t"}
    assert set(result.lock) == {"a", "t"}
    assert result.lock["a"] == LockEntry("object_store", "phys-a", ("a",), {})
    assert result.moved == []


def test_unchanged_graph_is_a_noop():
    lock = {"a": entry(physical="old-a")}
    result = run(spec(res("a")), lock)
    assert result.moved == []
    assert result.diagnostics == ()
    assert result.physical_names == {"a": "old-a"}
    assert result.lock == lock


def test_rename_inferred():
    lock = {"sensorhub-staging": entry(physical="p-staging", props={"x": 1})}
    result = run(spec(res("sensorhub-staging-v2", props={"x": 1})), lock)
    assert result.physical_names["sensorhub-staging-v2"] == "p-staging"
    assert result.moved == [("sensorhub-staging", "sensorhub-staging-v2")]
    assert [(d.code, d.severity) for d in result.diagnostics] == [("W_RENAME_INFERRED", "warning")]
    assert set(result.lock) == {"sensorhub-staging-v2"}
    assert result.lock["sensorhub-staging-v2"].physical_name == "p-staging"


def test_rename_with_usage_change_is_ambiguous():
    lock = {"old": entry(physical="p-old", sig=("a",))}
    result = run(spec(res("new", sig=("a", "b"))), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]
    assert "old" in result.errors[0].message and "new" in result.errors[0].message
    assert result.moved == []
    assert result.lock == lock


def test_rename_with_property_change_is_ambiguous():
    lock = {"old": entry(props={"x": 1})}
    result = run(spec(res("new", props={"x": 2})), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]


def test_two_removed_one_added_is_ambiguous():
    lock = {"o1": entry(physical="p1"), "o2": entry(physical="p2")}
    result = run(spec(res("n")), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]
    message = result.errors[0].message
    assert "o1" in message and "o2" in message and "n" in message
    assert result.moved == []
    assert result.lock == lock


def test_one_removed_two_added_is_ambiguous():
    lock = {"o": entry(physical="p")}
    result = run(spec(res("n1"), res("n2")), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]
    message = result.errors[0].message
    assert "o" in message and "n1" in message and "n2" in message
    assert result.moved == []


def test_bucket_and_topic_renamed_together():
    lock = {
        "b-old": entry(physical="p-b"),
        "t-old": entry("topic", "p-t", sig=("x",)),
    }
    graph = spec(res("b-new"), res("t-new", "topic", sig=("x",)))
    result = run(graph, lock)
    assert result.errors == ()
    assert sorted(result.moved) == [("b-old", "b-new"), ("t-old", "t-new")]
    assert result.physical_names == {"b-new": "p-b", "t-new": "p-t"}
    assert codes(result) == ["W_RENAME_INFERRED", "W_RENAME_INFERRED"]


def test_was_overrides_signature_change():
    lock = {"old": entry(physical="p-old", sig=("a",))}
    result = run(spec(res("new", sig=("a", "b"), was="old")), lock)
    assert result.errors == ()
    assert result.moved == [("old", "new")]
    assert [d.code for d in result.diagnostics] == ["W_RENAME_DECLARED"]
    assert result.physical_names == {"new": "p-old"}
    assert result.lock["new"].signature == ("a", "b")
    assert "old" not in result.lock


def test_was_unknown_source():
    lock = {"other": entry()}
    result = run(spec(res("other"), res("new", was="ghost")), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_UNKNOWN_SOURCE"]
    assert "ghost" in result.errors[0].message
    assert result.lock == lock


def test_stale_was_after_rename_is_ignored():
    lock = {"new": entry(physical="p")}
    result = run(spec(res("new", was="old")), lock)
    assert result.diagnostics == ()
    assert result.moved == []


def test_was_resolves_otherwise_ambiguous_group():
    lock = {"o1": entry(physical="p1"), "o2": entry(physical="p2")}
    result = run(spec(res("n", was="o1")), lock, destroy=frozenset({"o2"}))
    assert result.errors == ()
    assert result.moved == [("o1", "n")]


def test_stateful_removal_needs_destroy():
    lock = {"data": entry(physical="p-data")}
    result = run(spec(), lock)
    assert [d.code for d in result.errors] == ["E_STATEFUL_REMOVED"]
    assert "data" in result.errors[0].message
    assert result.lock == lock

    result = run(spec(), lock, destroy=frozenset({"data"}))
    assert result.errors == ()
    assert [(d.code, d.severity) for d in result.diagnostics] == [("W_RESOURCE_REMOVED", "warning")]
    assert "data" not in result.lock


def test_nonstateful_removal_warns():
    lock = {"events": entry("topic", "p-events")}
    result = run(spec(), lock)
    assert result.errors == ()
    assert [d.code for d in result.diagnostics] == ["W_RESOURCE_REMOVED"]
    assert result.lock == {}


def test_kind_change_is_error():
    lock = {"x": entry("queue", "p-x")}
    result = run(spec(res("x", "topic")), lock)
    assert [d.code for d in result.errors] == ["E_KIND_CHANGED"]
    assert result.lock == lock


def test_new_resource_next_to_existing_gets_derived_name():
    lock = {"a": entry(physical="old-a")}
    result = run(spec(res("a"), res("b", "topic")), lock)
    assert result.errors == ()
    assert result.physical_names == {"a": "old-a", "b": "phys-b"}
    assert set(result.lock) == {"a", "b"}


def test_properties_and_signature_updated_in_lock_for_same_id():
    lock = {"a": entry(sig=("a",), props={"x": 1})}
    result = run(spec(res("a", sig=("a", "b"), props={"x": 2})), lock)
    assert result.diagnostics == ()
    assert result.lock["a"] == LockEntry("object_store", "p", ("a", "b"), {"x": 2})


def test_result_is_identity_result():
    assert isinstance(run(spec(), {}), IdentityResult)


def test_corrupt_lock_raises(tmp_path):
    path = tmp_path / "pyifc.lock.json"
    good = {"kind": "topic", "physical_name": "p", "signature": [], "properties": {}}
    cases = [
        "{not json",
        json.dumps([]),
        json.dumps({"version": 2, "resources": {}}),
        json.dumps({"resources": {}}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "resources": []}),
        json.dumps({"version": 1, "resources": {"a": "x"}}),
        json.dumps({"version": 1, "resources": {"a": {k: v for k, v in good.items() if k != "kind"}}}),
        json.dumps({"version": 1, "resources": {"a": {**good, "signature": "x"}}}),
        json.dumps({"version": 1, "resources": {"a": {**good, "signature": [1]}}}),
        json.dumps({"version": 1, "resources": {"a": {**good, "properties": []}}}),
        json.dumps({"version": 1, "resources": {"a": {**good, "physical_name": 3}}}),
    ]
    for text in cases:
        path.write_text(text)
        with pytest.raises(LockError) as info:
            load_lock(path)
        assert str(path) in str(info.value), text


def test_missing_lock_is_none(tmp_path):
    assert load_lock(tmp_path / "pyifc.lock.json") is None


def test_lock_roundtrip_is_deterministic(tmp_path):
    path = tmp_path / "pyifc.lock.json"
    lock = {
        "z": entry("topic", "p-z", sig=("a", "b"), props={"y": 1, "x": [1, 2]}),
        "a": entry("object_store", "p-a"),
    }
    write_lock(path, lock)
    first = path.read_bytes()
    assert first.endswith(b"\n")
    data = json.loads(first)
    assert data["version"] == 1
    assert list(data["resources"]) == ["a", "z"]
    assert first.decode().startswith('{\n  "resources"')
    loaded = load_lock(path)
    assert loaded == lock
    write_lock(path, loaded)
    assert path.read_bytes() == first
    assert [p.name for p in tmp_path.iterdir()] == ["pyifc.lock.json"]


def test_unrelated_nonstateful_swap_is_ambiguous():
    lock = {"a": entry("topic", "p-a", sig=("x",), props={"p": 1})}
    result = run(spec(res("b", "topic", sig=("y",), props={"p": 2})), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]
    assert "a" in result.errors[0].message and "b" in result.errors[0].message
    assert result.lock == lock


def test_ambiguous_message_mentions_was_and_destroy():
    result = run(spec(res("n1"), res("n2")), {"o": entry()})
    message = result.errors[0].message
    assert "was=" in message and "--destroy" in message


def test_was_naming_still_declared_id():
    lock = {"old": entry(physical="p-old")}
    result = run(spec(res("old"), res("new", was="old")), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_UNKNOWN_SOURCE"]
    assert "still declared" in result.errors[0].message
    assert result.moved == []
    assert result.lock == lock


def test_two_resources_claiming_same_was():
    lock = {"old": entry("topic", "p-old")}
    result = run(spec(res("n1", "topic", was="old"), res("n2", "topic", was="old")), lock)
    assert [d.code for d in result.errors] == ["E_RENAME_AMBIGUOUS"]
    assert "n1" in result.errors[0].message and "n2" in result.errors[0].message
    assert result.moved == []
    assert result.lock == lock


def test_was_source_of_different_kind():
    lock = {"old": entry("queue", "p-old")}
    result = run(spec(res("new", "topic", was="old")), lock)
    assert [d.code for d in result.errors] == ["E_KIND_CHANGED"]
    assert result.moved == []
    assert result.lock == lock


def test_destroy_takes_matching_pair_out_of_rename_inference():
    lock = {"old": entry(physical="p-old")}
    result = run(spec(res("new")), lock, destroy=frozenset({"old"}))
    assert result.errors == ()
    assert result.moved == []
    assert [d.code for d in result.diagnostics] == ["W_RESOURCE_REMOVED"]
    assert result.physical_names == {"new": "phys-new"}
    assert set(result.lock) == {"new"}


def test_lock_signature_is_canonically_sorted(tmp_path):
    first = run(spec(res("a", sig=("b", "a"))), {}, init=True)
    second = run(spec(res("a", sig=("a", "b"))), {}, init=True)
    assert first.lock["a"].signature == ("a", "b")
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    write_lock(one, first.lock)
    write_lock(two, second.lock)
    assert one.read_bytes() == two.read_bytes()


def test_non_utf8_lock_raises(tmp_path):
    path = tmp_path / "pyifc.lock.json"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(LockError) as info:
        load_lock(path)
    assert str(info.value).startswith(str(path))
