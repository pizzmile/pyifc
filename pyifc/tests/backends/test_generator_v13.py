import json

import pytest

from pyifc import generate_terraform
from pyifc.backends.aws import UnsupportedByTarget, implied


def edge(component, resource, operation, provenance="both"):
    return {
        "component_id": component,
        "resource_id": resource,
        "operation": operation,
        "provenance": provenance,
    }


def mini_spec(staging="sensorhub-staging", queue="sensorhub-jobs", worker="worker", versioned=True):
    """One http handler writing a bucket and publishing to a topic, one queue worker."""
    return {
        "name": "sensorhub",
        "resources": [
            {
                "id": staging,
                "kind": "object_store",
                "overrides": {"aws": {"versioning": True}} if versioned else {},
            },
            {"id": queue, "kind": "queue"},
            {"id": "sensorhub-events", "kind": "topic"},
        ],
        "components": [
            {
                "id": "ingest",
                "kind": "http_handler",
                "callable_ref": "app:ingest",
                "properties": {"method": "POST", "path": "/in"},
            },
            {
                "id": worker,
                "kind": "worker",
                "callable_ref": "app:work",
                "trigger": f"queue:{queue}",
            },
        ],
        "capabilities": [
            edge("ingest", staging, "write"),
            edge("ingest", "sensorhub-events", "publish"),
            edge(worker, queue, "subscribe", "declared"),
        ],
    }


def policy(document, name):
    return json.loads(document["resource"]["aws_iam_role_policy"][name]["policy"])


def actions(document, name):
    return {a for s in policy(document, name)["Statement"] for a in s["Action"]}


def addresses(document):
    return {
        f"{kind}.{name}"
        for kind, items in document["resource"].items()
        for name in items
    }


def test_default_mode_drops_unused_grant():
    spec = mini_spec()
    spec["capabilities"][0]["provenance"] = "declared"
    assert "s3:PutObject" not in actions(generate_terraform(spec), "ingest")
    assert "s3:PutObject" in actions(generate_terraform(spec, iam_mode="declared"), "ingest")


def test_spec_without_provenance_is_rejected():
    spec = mini_spec()
    del spec["capabilities"][0]["provenance"]
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(spec)
    assert "provenance" in str(caught.value)


def test_implied_actions_added(monkeypatch):
    monkeypatch.setitem(implied.IMPLIED_ACTIONS, ("topic", "publish"), ("sns:GetTopicAttributes",))
    spec = mini_spec()
    assert all(item["provenance"] != "implied" for item in spec["capabilities"])
    document = generate_terraform(spec)
    assert "sns:GetTopicAttributes" in actions(document, "ingest")
    assert "sns:GetTopicAttributes" not in actions(document, "worker")


def test_implied_actions_only_for_kept_edges(monkeypatch):
    monkeypatch.setitem(implied.IMPLIED_ACTIONS, ("object_store", "write"), ("s3:AbortMultipartUpload",))
    spec = mini_spec()
    spec["capabilities"][0]["provenance"] = "declared"
    assert "s3:AbortMultipartUpload" not in actions(generate_terraform(spec), "ingest")
    assert "s3:AbortMultipartUpload" in actions(generate_terraform(spec, iam_mode="declared"), "ingest")


def test_implied_actions_read_from_runtime_required_actions(monkeypatch):
    from pyifc.runtime import aws_adapters as aws_runtime

    monkeypatch.setattr(aws_runtime.SNSTopic, "REQUIRED_ACTIONS", {"publish": ("sns:Foo",)}, raising=False)
    monkeypatch.setattr(implied, "IMPLIED_ACTIONS", {})
    monkeypatch.setattr(implied, "_loaded", False, raising=False)
    assert implied.implied_actions("topic", "publish") == ("sns:Foo",)
    assert implied.implied_actions("queue", "nonexistent") == ()


def test_pinned_physical_name():
    plain = generate_terraform(mini_spec())
    pinned = generate_terraform(mini_spec(), physical_names={"sensorhub-staging": "p-staging"})
    assert plain["resource"]["aws_s3_bucket"]["sensorhub_staging"]["bucket"] == "${var.name_prefix}-staging"
    assert pinned["resource"]["aws_s3_bucket"]["sensorhub_staging"]["bucket"] == "p-staging"
    assert addresses(pinned) == addresses(plain)
    assert pinned["resource"]["aws_sqs_queue"] == plain["resource"]["aws_sqs_queue"]


def test_moved_entries_cover_all_renamed_addresses():
    old = generate_terraform(mini_spec())
    new = generate_terraform(
        mini_spec(staging="sensorhub-raw", queue="sensorhub-tasks", worker="job_worker"),
        moved=[
            ("sensorhub-staging", "sensorhub-raw"),
            ("sensorhub-jobs", "sensorhub-tasks"),
            ("worker", "job_worker"),
        ],
    )
    entries = new["moved"]
    assert "moved" not in old
    froms = {item["from"] for item in entries}
    tos = {item["to"] for item in entries}
    assert all(set(item) == {"from", "to"} for item in entries)
    assert len(froms) == len(entries)
    assert (addresses(old) - addresses(new)) <= froms
    assert froms <= addresses(old)
    assert tos <= addresses(new)
    assert not froms & addresses(new)
    assert "aws_s3_bucket_versioning.sensorhub_staging" in froms
    assert "aws_lambda_event_source_mapping.worker" in froms


def test_moved_omitted_when_nothing_renamed():
    assert "moved" not in generate_terraform(mini_spec(), moved=[])


def test_moved_with_unknown_new_id_is_rejected():
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(mini_spec(), moved=[("zzz", "yyy")])
    assert "yyy" in str(caught.value)
    # reported together with the other problems
    spec = mini_spec()
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(spec, moved=[("zzz", "yyy")], physical_names={"nope": "x"})
    assert len(caught.value.problems) == 2


def test_moved_with_identical_ids_is_rejected():
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(mini_spec(), moved=[("sensorhub-staging", "sensorhub-staging")])
    assert "identical" in str(caught.value)


@pytest.mark.parametrize("provenance", ["inferred", "bogus", 3])
def test_unknown_provenance_value_is_rejected(provenance):
    spec = mini_spec()
    spec["capabilities"][0]["provenance"] = provenance
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(spec)
    assert "provenance" in str(caught.value)


@pytest.mark.parametrize("provenance", ["declared", "used", "both", "implied"])
def test_known_provenance_values_are_accepted(provenance):
    spec = mini_spec()
    spec["capabilities"][0]["provenance"] = provenance
    generate_terraform(spec)


def full_spec(topic="sensorhub-events", schedule="sensorhub-nightly", job="job", listener="listener"):
    """Topic with a topic-triggered worker, schedule with its job, plus a versioned bucket and queue worker."""
    spec = mini_spec()
    spec["resources"] = [item for item in spec["resources"] if item["kind"] != "topic"]
    spec["resources"] += [
        {"id": topic, "kind": "topic"},
        {"id": schedule, "kind": "schedule", "properties": {"cron": "0 3 * * *"}},
    ]
    spec["components"] += [
        {"id": listener, "kind": "worker", "callable_ref": "app:listen", "trigger": f"topic:{topic}"},
        {"id": job, "kind": "scheduled_job", "callable_ref": "app:nightly", "trigger": f"schedule:{schedule}"},
    ]
    spec["capabilities"] = [
        edge("ingest", "sensorhub-staging", "write"),
        edge("ingest", topic, "publish"),
        edge("worker", "sensorhub-jobs", "subscribe", "declared"),
        edge(listener, topic, "subscribe", "declared"),
    ]
    return spec


RENAMES = {
    "versioned bucket": {"staging": "sensorhub-raw"},
    "queue with mapping": {"queue": "sensorhub-tasks", "worker": "job_worker"},
    "topic with subscription and permission": {"topic": "sensorhub-bus", "listener": "bus_listener"},
    "schedule with target and permission": {"schedule": "sensorhub-weekly", "job": "weekly_job"},
}


def renamed_full_spec(changes):
    old_ids = {
        "staging": "sensorhub-staging", "queue": "sensorhub-jobs", "worker": "worker",
        "topic": "sensorhub-events", "listener": "listener",
        "schedule": "sensorhub-nightly", "job": "job",
    }
    mapping = {old_ids[key]: value for key, value in changes.items()}
    spec = full_spec(
        topic=changes.get("topic", "sensorhub-events"),
        schedule=changes.get("schedule", "sensorhub-nightly"),
        job=changes.get("job", "job"),
        listener=changes.get("listener", "listener"),
    )
    spec = rename_in(spec, mapping)
    return spec, sorted(mapping.items())


def rename_in(spec, mapping):
    """Rename ids in a full_spec() built with default ids (topic/schedule/job/listener already renamed)."""
    for item in spec["resources"]:
        item["id"] = mapping.get(item["id"], item["id"])
    for item in spec["components"]:
        item["id"] = mapping.get(item["id"], item["id"])
        if item.get("trigger"):
            prefix, _, target = item["trigger"].partition(":")
            item["trigger"] = f"{prefix}:{mapping.get(target, target)}"
    for item in spec["capabilities"]:
        item["component_id"] = mapping.get(item["component_id"], item["component_id"])
        item["resource_id"] = mapping.get(item["resource_id"], item["resource_id"])
    return spec


@pytest.mark.parametrize("case", sorted(RENAMES))
def test_moved_set_property_per_dependent_kind(case):
    old = generate_terraform(full_spec())
    new_spec, pairs = renamed_full_spec(RENAMES[case])
    new = generate_terraform(new_spec, moved=pairs)
    froms = {item["from"] for item in new["moved"]}
    tos = {item["to"] for item in new["moved"]}
    assert (addresses(old) - addresses(new)) <= froms
    assert froms <= addresses(old)
    assert tos <= addresses(new)
    assert not froms & addresses(new)
    expected = {
        "versioned bucket": {"aws_s3_bucket", "aws_s3_bucket_versioning"},
        "queue with mapping": {"aws_sqs_queue", "aws_lambda_event_source_mapping"},
        "topic with subscription and permission": {
            "aws_sns_topic", "aws_sns_topic_subscription", "aws_lambda_permission",
        },
        "schedule with target and permission": {
            "aws_cloudwatch_event_rule", "aws_cloudwatch_event_target", "aws_lambda_permission",
        },
    }[case]
    assert expected <= {address.split(".")[0] for address in froms}


def test_package_hash_literal():
    literal = generate_terraform(mini_spec(), package_hash="abc=")
    for function in literal["resource"]["aws_lambda_function"].values():
        assert function["source_code_hash"] == "abc="
    default = generate_terraform(mini_spec())
    for function in default["resource"]["aws_lambda_function"].values():
        assert function["source_code_hash"] == "${filebase64sha256(var.lambda_package)}"


def test_uniform_entrypoint():
    document = generate_terraform(mini_spec())
    functions = document["resource"]["aws_lambda_function"]
    assert set(functions) == {"ingest", "worker"}
    for name, function in functions.items():
        assert function["handler"] == "pyifc.runtime.entry.handler"
        variables = function["environment"]["variables"]
        assert variables["PYIFC_COMPONENT"] == name
        assert variables["PYIFC_APP_MODULE"] == "app"


def test_runtime_components_default_is_all_and_handler_override_works():
    document = generate_terraform(mini_spec(), handler_overrides={"worker": "custom.handler"})
    functions = document["resource"]["aws_lambda_function"]
    assert functions["worker"]["handler"] == "custom.handler"
    assert functions["ingest"]["handler"] == "pyifc.runtime.entry.handler"
    assert functions["ingest"]["environment"]["variables"]["PYIFC_TARGET"] == "aws"
    only = generate_terraform(mini_spec(), runtime_components={"ingest"})
    assert "PYIFC_TARGET" not in only["resource"]["aws_lambda_function"]["worker"]["environment"]["variables"]


def test_physical_name_collision_rejected():
    with pytest.raises(UnsupportedByTarget) as caught:
        generate_terraform(
            mini_spec(), physical_names={"sensorhub-staging": "same", "sensorhub-jobs": "same"}
        )
    assert "same" in str(caught.value)
