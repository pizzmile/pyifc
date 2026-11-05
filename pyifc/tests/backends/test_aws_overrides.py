import copy
import json

import pytest

from pyifc import (
    SUPPORTED_OVERRIDES,
    Application,
    Bucket,
    Queue,
    UnsupportedByTarget,
    analyze_source,
    generate_terraform,
)

SOURCE = """
from pyifc import Application, Bucket, Queue

app = Application("demo")
uploads = Bucket("uploads", overrides={"aws": {"versioning": True, "tags": {"team": "iot"}}})
jobs = Queue("jobs", overrides={"aws": {"visibility_timeout_seconds": 60}})


@app.handler(method="POST", path="/up")
@app.write(uploads)
@app.publish(jobs)
def up(request):
    uploads.put("k", b"v")
    jobs.publish(b"v")


@app.worker()
@app.subscribe(jobs)
def work(message):
    jobs.receive()
"""


def declared_spec():
    result = analyze_source(SOURCE, filename="app.py")
    assert result.specification is not None, result.to_json()
    return result.specification


def without_overrides(specification):
    plain = copy.deepcopy(specification)
    for resource in plain["resources"]:
        resource.pop("overrides", None)
    return plain


def iam_blocks(document):
    resources = document["resource"]
    return {kind: resources[kind] for kind in ("aws_iam_role", "aws_iam_role_policy")}


def problems_of(specification, **kwargs):
    with pytest.raises(UnsupportedByTarget) as error:
        generate_terraform(specification, **kwargs)
    return error.value.problems


def test_declared_overrides_are_merged_into_resources():
    resources = generate_terraform(declared_spec())["resource"]

    assert resources["aws_s3_bucket"]["uploads"]["tags"] == {"team": "iot"}
    assert resources["aws_s3_bucket_versioning"]["uploads"] == {
        "bucket": "${aws_s3_bucket.uploads.id}",
        "versioning_configuration": {"status": "Enabled"},
    }
    queue = resources["aws_sqs_queue"]["jobs"]
    assert queue["visibility_timeout_seconds"] == 60
    assert queue["name"] == "${var.name_prefix}-jobs"
    # Stateful protection is independent of overrides.
    assert resources["aws_s3_bucket"]["uploads"]["lifecycle"] == {"prevent_destroy": True}


def test_passed_overrides_layer_over_declared_ones():
    document = generate_terraform(
        declared_spec(),
        resource_overrides={
            "jobs": {"visibility_timeout_seconds": 90, "delay_seconds": 5},
            "uploads": {"versioning": False},
        },
    )
    resources = document["resource"]

    assert resources["aws_sqs_queue"]["jobs"]["visibility_timeout_seconds"] == 90
    assert resources["aws_sqs_queue"]["jobs"]["delay_seconds"] == 5
    assert resources["aws_s3_bucket_versioning"]["uploads"]["versioning_configuration"] == {
        "status": "Suspended"
    }
    assert resources["aws_s3_bucket"]["uploads"]["tags"] == {"team": "iot"}


def test_no_overrides_leaves_specification_and_output_unchanged():
    plain = without_overrides(declared_spec())
    assert all("overrides" not in resource for resource in plain["resources"])

    app = Application("demo")
    app.registry.register_resource(Bucket("b").to_declaration())
    app.registry.register_resource(Queue("q").to_declaration())
    assert all("overrides" not in resource for resource in app.to_spec()["resources"])

    resources = generate_terraform(plain)["resource"]
    assert resources["aws_sqs_queue"]["jobs"]["visibility_timeout_seconds"] == 180
    assert "aws_s3_bucket_versioning" not in resources
    assert "tags" not in resources["aws_s3_bucket"]["uploads"]


def test_runtime_declaration_carries_overrides_into_specification():
    app = Application("demo")
    app.registry.register_resource(
        Queue("jobs", overrides={"aws": {"delay_seconds": 3}}).to_declaration()
    )

    assert app.to_spec()["resources"] == [
        {
            "id": "jobs",
            "kind": "queue",
            "properties": {},
            "stateful": False,
            "overrides": {"aws": {"delay_seconds": 3}},
            "use_signature": [],
        }
    ]


def test_iam_is_byte_identical_with_and_without_overrides():
    specification = declared_spec()
    with_overrides = generate_terraform(
        specification, resource_overrides={"jobs": {"delay_seconds": 5}}
    )
    baseline = generate_terraform(without_overrides(specification))

    assert json.dumps(iam_blocks(with_overrides), sort_keys=True) == json.dumps(
        iam_blocks(baseline), sort_keys=True
    )
    assert with_overrides["resource"]["aws_lambda_function"] == baseline["resource"]["aws_lambda_function"]


def test_output_is_deterministic():
    specification = declared_spec()
    overrides = {"jobs": {"delay_seconds": 5, "tags": {"b": "2", "a": "1"}}}
    first = generate_terraform(specification, resource_overrides=overrides)
    second = generate_terraform(copy.deepcopy(specification), resource_overrides=copy.deepcopy(overrides))

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    # Generating must not mutate the caller's specification or overrides.
    assert specification == declared_spec()
    assert overrides == {"jobs": {"delay_seconds": 5, "tags": {"b": "2", "a": "1"}}}


@pytest.mark.parametrize(
    "attribute",
    [
        "policy",
        "role",
        "iam_role_arn",
        "assume_role_policy",
        "redrive_allow_policy",
        "grant",
        "acl",
        "Policy",
        "principal",
        "permissions",
    ],
)
def test_iam_touching_overrides_are_rejected_with_explicit_message(attribute):
    specification = declared_spec()
    for resource in specification["resources"]:
        resource["overrides"] = {"aws": {attribute: "anything"}}

    problems = problems_of(specification)

    assert len(problems) == 2
    assert all("touches IAM/permissions" in problem for problem in problems)


def test_iam_override_passed_to_generator_is_rejected():
    problems = problems_of(
        declared_spec(),
        resource_overrides={"uploads": {"policy": "{}"}, "jobs": {"visibility_timeout_seconds": 30}},
    )

    assert problems == [
        "resource 'uploads': passed override 'policy' touches IAM/permissions, which overrides "
        "may never change; permissions come only from declared capabilities"
    ]


def test_override_cannot_add_grants_through_new_capabilities():
    problems = problems_of(
        declared_spec(),
        resource_overrides={"jobs": {"grants": [{"component": "up", "operation": "subscribe"}]}},
    )

    assert len(problems) == 1 and "touches IAM/permissions" in problems[0]


def test_override_for_unknown_resource_is_rejected():
    problems = problems_of(declared_spec(), resource_overrides={"ghost": {"tags": {"a": "b"}}})

    assert problems == ["override for unknown resource 'ghost'"]


@pytest.mark.parametrize(
    "resource_id, attribute",
    [
        ("jobs", "name"),
        ("jobs", "fifo_queue"),
        ("uploads", "bucket"),
        ("uploads", "lifecycle"),
        ("uploads", "force_destroy"),
        ("uploads", "visibility_timeout_seconds"),
        ("jobs", "versioning"),
    ],
)
def test_non_allowlisted_attributes_are_rejected(resource_id, attribute):
    problems = problems_of(declared_spec(), resource_overrides={resource_id: {attribute: 1}})

    assert len(problems) == 1
    assert f"'{attribute}' is not supported" in problems[0]
    assert "supported:" in problems[0]


@pytest.mark.parametrize(
    "resource_id, overrides",
    [
        ("jobs", {"visibility_timeout_seconds": "60"}),
        ("jobs", {"visibility_timeout_seconds": True}),
        ("jobs", {"visibility_timeout_seconds": 10**6}),
        ("jobs", {"tags": ["a"]}),
        ("jobs", {"tags": {"aws:reserved": "x"}}),
        ("jobs", {"tags": {"team": "${aws_iam_role.up.arn}"}}),
        ("uploads", {"versioning": "yes"}),
    ],
)
def test_invalid_override_values_are_rejected(resource_id, overrides):
    assert len(problems_of(declared_spec(), resource_overrides={resource_id: overrides})) == 1


def test_all_problems_are_reported_together_and_nothing_is_emitted():
    specification = declared_spec()
    specification["resources"].append(
        {"id": "t", "kind": "schedule", "properties": {"cron": "0 3 * * 1"}, "stateful": False}
    )

    problems = problems_of(
        specification,
        resource_overrides={"ghost": {}, "uploads": {"policy": "x"}, "jobs": {"nope": 1}},
    )

    assert len(problems) == 4  # unsupported schedule expression + unknown + IAM + non-allowlisted


def test_allowlist_contains_no_iam_looking_attribute():
    from pyifc.backends.aws.overrides import _IAM_ATTRIBUTE

    for attributes in SUPPORTED_OVERRIDES.values():
        assert not any(_IAM_ATTRIBUTE.search(name) for name in attributes)


def test_declaration_rejects_malformed_overrides():
    with pytest.raises(ValueError):
        Bucket("b", overrides={"aws": ["tags"]})
    with pytest.raises(ValueError):
        Queue("q", overrides={"aws": {"x": {1, 2}}})


@pytest.mark.parametrize(
    "call",
    [
        'Bucket("b", overrides={"aws": some_variable})',
        'Bucket("b", overrides={"aws": ["tags"]})',
        'Queue("b", overrides=["aws"])',
        'Schedule("b", cron="0 3 * * *", overrides={"aws": {}})',
    ],
)
def test_analyzer_rejects_non_literal_or_misplaced_overrides(call):
    source = (
        "from pyifc import Application, Bucket, Queue, Schedule\n"
        'app = Application("demo")\n'
        f"b = {call}\n"
    )

    result = analyze_source(source)

    assert result.specification is None
    assert any(
        item.code == "E_RESOURCE_DECLARATION" and "Invalid overrides" in item.message
        for item in result.errors
    )


def test_visibility_timeout_below_worker_timeout_is_rejected_for_consumed_queue():
    problems = problems_of(
        declared_spec(), resource_overrides={"jobs": {"visibility_timeout_seconds": 10}}
    )
    assert any("below the 30s timeout" in problem for problem in problems)


def test_visibility_timeout_at_worker_timeout_is_accepted():
    document = generate_terraform(
        declared_spec(), resource_overrides={"jobs": {"visibility_timeout_seconds": 30}}
    )
    assert document["resource"]["aws_sqs_queue"]["jobs"]["visibility_timeout_seconds"] == 30


def test_short_visibility_timeout_is_allowed_for_queue_without_worker():
    specification = declared_spec()
    specification["components"] = [
        component for component in specification["components"] if component["kind"] != "worker"
    ]
    specification["capabilities"] = [
        capability
        for capability in specification["capabilities"]
        if capability["operation"] != "subscribe"
    ]
    document = generate_terraform(
        specification, resource_overrides={"jobs": {"visibility_timeout_seconds": 10}}
    )
    assert document["resource"]["aws_sqs_queue"]["jobs"]["visibility_timeout_seconds"] == 10


def test_unknown_override_target_is_rejected_in_declaration_and_specification():
    with pytest.raises(ValueError, match="unknown override target 'aw'"):
        Bucket("b", overrides={"aw": {"versioning": True}})
    specification = declared_spec()
    specification["resources"][0]["overrides"] = {"aw": {"versioning": True}}
    assert any("unknown override target 'aw'" in problem for problem in problems_of(specification))


def test_tags_override_can_be_declared_on_every_allowlisted_kind():
    from pyifc import KeyValueStore, Secret, Topic

    for resource in (KeyValueStore("k", overrides={"aws": {"tags": {"a": "b"}}}),
                     Topic("t", overrides={"aws": {"tags": {"a": "b"}}}),
                     Secret("s", overrides={"aws": {"tags": {"a": "b"}}})):
        assert resource.to_declaration().overrides == {"aws": {"tags": {"a": "b"}}}
