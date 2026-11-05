"""Terraform JSON for AWS from a checked specification."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyifc.backends.aws.emitters import (
    NEW_KIND_EMITTERS,
    emit_schedule_targets,
    emit_topic_subscriptions,
)
from pyifc.backends.aws.mapping import (
    ENTRY_HANDLER,
    KIND_ENVIRONMENT,
    KIND_PROVIDER_ENDPOINTS,
    LAMBDA_TIMEOUT_SECONDS,
    TERRAFORM_TYPES,
)
from pyifc.backends.aws.moved import build_moved_entries, moved_problems
from pyifc.backends.aws.naming import physical_name, physical_name_problems
from pyifc.backends.aws.overrides import override_problems
from pyifc.backends.aws.policy import component_policy, lambda_trust_policy
from pyifc.backends.aws.validation import (
    UnsupportedByTarget,
    provenance_problems,
    unsupported_problems,
    validate_specification,
)
from pyifc.backends.iam_mode import DEFAULT_IAM_MODE, split_capabilities
from pyifc.core.naming import terraform_name


def generate_terraform(
    specification: dict[str, Any],
    *,
    lambda_package: str = "sensor-hub.zip",
    handler_overrides: dict[str, str] | None = None,
    region: str = "us-east-1",
    endpoint: str | None = None,
    lambda_environment: dict[str, str] | None = None,
    runtime_components: set[str] | None = None,
    protect_stateful: bool = True,
    resource_overrides: dict[str, dict[str, Any]] | None = None,
    iam_mode: str = DEFAULT_IAM_MODE,
    floci_sns_publish_wildcard: bool = False,
    physical_names: dict[str, str] | None = None,
    moved: Sequence[tuple[str, str]] = (),
    package_hash: str | None = None,
) -> dict[str, Any]:
    """Generate AWS Terraform JSON from a checked pyifc specification.

    Raises UnsupportedByTarget (listing all problems) before emitting anything
    if the specification is outside SUPPORTED_CAPABILITIES or its overrides
    are outside SUPPORTED_OVERRIDES. Resources marked ``stateful`` get
    ``lifecycle.prevent_destroy`` unless protect_stateful is False.

    ``resource_overrides`` maps a resource id to "aws" attribute overrides and
    is layered over those declared on the resource. Overrides never affect the
    generated IAM.

    ``iam_mode`` ("used" default, or "declared") selects which capability edges
    become IAM grants; see pyifc.backends.iam_mode. Unknown values raise ValueError.
    Every capability edge must carry ``provenance``. For each kept edge the
    policy also gets the actions of ``pyifc.backends.aws.implied.implied_actions``.

    ``physical_names`` maps a resource id to the physical name that replaces
    the default ``${var.name_prefix}-<suffix>``; the Terraform address does not
    change. Two resources resolving to one physical name are rejected.

    ``moved`` is a sequence of (old id, new id) pairs for renamed resources or
    components. Every emitted address that embeds the new Terraform name gets a
    top-level ``moved`` entry from the same address under the old name. The key
    is omitted when there is nothing to move. The new id must exist in the
    specification and differ from the old one. Renaming a component only avoids
    address churn for its role, function and triggers because their names
    derive from the component id, so they are moved together with it.

    ``package_hash`` is written as a literal ``source_code_hash`` instead of
    ``filebase64sha256(var.lambda_package)``.

    Every Lambda uses ``pyifc.runtime.entry.handler`` (``handler_overrides`` still wins)
    with ``PYIFC_COMPONENT`` and ``PYIFC_APP_MODULE`` set. ``runtime_components``
    defaults to all components.

    ``floci_sns_publish_wildcard`` (default False) emits ``Resource: "*"`` for
    ``sns:Publish`` so a topic chain can run under Floci's IAM enforcement,
    which denies ARN-scoped publish. It is an emulator workaround and widens
    the policy; leave it off for anything but Floci.
    """
    validate_specification(specification)
    problems = (
        provenance_problems(specification)
        + moved_problems(specification, moved)
        + unsupported_problems(specification)
        + override_problems(specification, resource_overrides)
        + physical_name_problems(specification, physical_names)
    )
    if problems:
        raise UnsupportedByTarget(problems)
    policy_capabilities, _ = split_capabilities(specification, iam_mode)
    layout = _Layout.of(specification)
    document = _base_document(specification["name"], lambda_package, region)
    blocks = document["resource"]

    _emit_resources(
        blocks, layout, specification["name"],
        resource_overrides=resource_overrides or {},
        physical_names=physical_names,
        protect_stateful=protect_stateful,
    )
    _emit_functions(
        blocks, layout, specification["capabilities"], policy_capabilities,
        handler_overrides=handler_overrides or {},
        lambda_environment=lambda_environment or {},
        runtime_components=(
            runtime_components
            if runtime_components is not None
            else {component["id"] for component in layout.components}
        ),
        package_hash=package_hash,
        floci_sns_publish_wildcard=floci_sns_publish_wildcard,
    )
    _emit_api(document, layout)
    _emit_queue_triggers(blocks, layout)
    emit_topic_subscriptions(blocks, layout.components, layout.component_names, layout.names)
    emit_schedule_targets(blocks, layout.components, layout.component_names, layout.names)
    if endpoint is not None:
        _point_provider_at(document["provider"]["aws"], layout, endpoint)

    moved_entries = build_moved_entries(blocks, moved)
    if moved_entries:
        document["moved"] = moved_entries
    return document


@dataclass(frozen=True)
class _Layout:
    """The specification indexed by id, with the Terraform name of every resource and component."""

    resources: dict[str, dict[str, Any]]
    components: list[dict[str, Any]]
    names: dict[str, str]
    component_names: dict[str, str]

    @classmethod
    def of(cls, specification: dict[str, Any]) -> _Layout:
        resources = {resource["id"]: resource for resource in specification["resources"]}
        components = specification["components"]
        return cls(
            resources=resources,
            components=components,
            names={resource_id: terraform_name(resource_id) for resource_id in resources},
            component_names={component["id"]: terraform_name(component["id"]) for component in components},
        )


def _base_document(application_name: str, lambda_package: str, region: str) -> dict[str, Any]:
    return {
        "terraform": {
            "required_version": ">= 1.10, < 2.0",
            "required_providers": {
                "aws": {
                    "source": "hashicorp/aws",
                    "version": "~> 6.0",
                }
            },
        },
        "variable": {
            "name_prefix": {
                "type": "string",
                "default": application_name,
            },
            "lambda_package": {
                "type": "string",
                "default": lambda_package,
            },
        },
        "provider": {
            "aws": {
                "region": region,
            }
        },
        "resource": {},
        "output": {},
    }


def _emit_resources(
    blocks: dict[str, Any],
    layout: _Layout,
    application_name: str,
    *,
    resource_overrides: dict[str, dict[str, Any]],
    physical_names: dict[str, str] | None,
    protect_stateful: bool,
) -> None:
    for resource_id, resource in layout.resources.items():
        name = layout.names[resource_id]
        kind = resource["kind"]
        protected = protect_stateful and bool(resource.get("stateful"))
        overrides = {
            **resource.get("overrides", {}).get("aws", {}),
            **resource_overrides.get(resource_id, {}),
        }
        if kind == "object_store":
            blocks.setdefault("aws_s3_bucket", {})[name] = {
                "bucket": physical_name(resource_id, application_name, physical_names),
            }
            if "versioning" in overrides:
                status = "Enabled" if overrides.pop("versioning") else "Suspended"
                blocks.setdefault("aws_s3_bucket_versioning", {})[name] = {
                    "bucket": f"${{aws_s3_bucket.{name}.id}}",
                    "versioning_configuration": {"status": status},
                }
            blocks["aws_s3_bucket"][name].update(overrides)
        elif kind == "queue":
            blocks.setdefault("aws_sqs_queue", {})[name] = {
                "name": physical_name(resource_id, application_name, physical_names),
                "visibility_timeout_seconds": 180,
                **overrides,
            }
        elif kind in NEW_KIND_EMITTERS:
            NEW_KIND_EMITTERS[kind](
                blocks, name,
                physical_name(resource_id, application_name, physical_names),
                resource, overrides, protected,
            )
        if protected:
            block = blocks[TERRAFORM_TYPES[kind]][name]
            block["lifecycle"] = {"prevent_destroy": True}


def _emit_functions(
    blocks: dict[str, Any],
    layout: _Layout,
    all_capabilities: list[dict[str, Any]],
    policy_capabilities: list[dict[str, Any]],
    *,
    handler_overrides: dict[str, str],
    lambda_environment: dict[str, str],
    runtime_components: set[str],
    package_hash: str | None,
    floci_sns_publish_wildcard: bool,
) -> None:
    """One role, one policy and one Lambda function per component."""
    for component in layout.components:
        component_id = component["id"]
        name = layout.component_names[component_id]
        blocks.setdefault("aws_iam_role", {})[name] = {
            "name": f"${{var.name_prefix}}-{name}",
            "assume_role_policy": json.dumps(lambda_trust_policy(), separators=(",", ":")),
        }
        policy = component_policy(
            component_id, policy_capabilities, layout.resources, layout.names, floci_sns_publish_wildcard
        )
        blocks.setdefault("aws_iam_role_policy", {})[name] = {
            "name": f"${{var.name_prefix}}-{name}-access",
            "role": f"${{aws_iam_role.{name}.id}}",
            "policy": json.dumps(policy, separators=(",", ":")),
        }
        function = {
            "function_name": f"${{var.name_prefix}}-{name}",
            "filename": "${var.lambda_package}",
            "source_code_hash": package_hash or "${filebase64sha256(var.lambda_package)}",
            "role": f"${{aws_iam_role.{name}.arn}}",
            "runtime": "python3.12",
            "handler": handler_overrides.get(component_id, ENTRY_HANDLER),
            "architectures": ["arm64"],
            "timeout": LAMBDA_TIMEOUT_SECONDS,
        }
        environment = dict(lambda_environment)
        environment["PYIFC_COMPONENT"] = component_id
        environment["PYIFC_APP_MODULE"] = component["callable_ref"].partition(":")[0]
        if component_id in runtime_components:
            environment["PYIFC_TARGET"] = "aws"
            environment.update(_resource_bindings(component_id, all_capabilities, layout))
        if environment:
            function["environment"] = {"variables": environment}
        blocks.setdefault("aws_lambda_function", {})[name] = function


def _resource_bindings(
    component_id: str, capabilities: list[dict[str, Any]], layout: _Layout
) -> dict[str, str]:
    """Environment variables that tell the runtime adapters where each declared resource lives."""
    bindings: dict[str, str] = {}
    for capability in capabilities:
        if capability["component_id"] != component_id:
            continue
        resource_id = capability["resource_id"]
        resource = layout.resources[resource_id]
        resource_name = layout.names[resource_id]
        slug = resource_id.upper().replace("-", "_")
        if resource["kind"] == "object_store":
            bindings[f"PYIFC_BUCKET_{slug}"] = f"${{aws_s3_bucket.{resource_name}.id}}"
        elif resource["kind"] == "queue":
            bindings[f"PYIFC_QUEUE_{slug}"] = f"${{aws_sqs_queue.{resource_name}.url}}"
        elif resource["kind"] in KIND_ENVIRONMENT:
            variable, attribute = KIND_ENVIRONMENT[resource["kind"]]
            bindings[f"{variable}{slug}"] = f"${{{TERRAFORM_TYPES[resource['kind']]}.{resource_name}.{attribute}}}"
    return bindings


def _emit_api(document: dict[str, Any], layout: _Layout) -> None:
    """An HTTP API with one route, integration and invoke permission per HTTP component."""
    api_components = [component for component in layout.components if component["kind"] == "http_handler"]
    if not api_components:
        return
    blocks = document["resource"]
    blocks.setdefault("aws_apigatewayv2_api", {})["app"] = {
        "name": "${var.name_prefix}",
        "protocol_type": "HTTP",
    }
    for component in api_components:
        name = layout.component_names[component["id"]]
        integration = f"${{aws_apigatewayv2_integration.{name}.id}}"
        blocks.setdefault("aws_apigatewayv2_integration", {})[name] = {
            "api_id": "${aws_apigatewayv2_api.app.id}",
            "integration_type": "AWS_PROXY",
            "integration_uri": f"${{aws_lambda_function.{name}.invoke_arn}}",
            "payload_format_version": "2.0",
        }
        route = component["properties"]
        blocks.setdefault("aws_apigatewayv2_route", {})[name] = {
            "api_id": "${aws_apigatewayv2_api.app.id}",
            "route_key": f"{route['method']} {route['path']}",
            "target": f"integrations/{integration}",
        }
        blocks.setdefault("aws_lambda_permission", {})[name] = {
            "statement_id": f"AllowApiGateway{name.title().replace('_', '')}",
            "action": "lambda:InvokeFunction",
            "function_name": f"${{aws_lambda_function.{name}.function_name}}",
            "principal": "apigateway.amazonaws.com",
            "source_arn": "${aws_apigatewayv2_api.app.execution_arn}/*/*",
        }
    blocks.setdefault("aws_apigatewayv2_stage", {})["default"] = {
        "api_id": "${aws_apigatewayv2_api.app.id}",
        "name": "$default",
        "auto_deploy": True,
    }
    document["output"]["api_endpoint"] = {"value": "${aws_apigatewayv2_api.app.api_endpoint}"}
    document["output"]["api_id"] = {"value": "${aws_apigatewayv2_api.app.id}"}


def _emit_queue_triggers(blocks: dict[str, Any], layout: _Layout) -> None:
    """An event source mapping from each queue to the worker that consumes it."""
    for component in layout.components:
        trigger = component.get("trigger", "")
        if component["kind"] != "worker" or not trigger.startswith("queue:"):
            continue
        queue_id = trigger.removeprefix("queue:")
        component_name = layout.component_names[component["id"]]
        queue_name = layout.names[queue_id]
        blocks.setdefault("aws_lambda_event_source_mapping", {})[component_name] = {
            "event_source_arn": f"${{aws_sqs_queue.{queue_name}.arn}}",
            "function_name": f"${{aws_lambda_function.{component_name}.arn}}",
            "batch_size": 1,
            "enabled": True,
        }


def _point_provider_at(provider: dict[str, Any], layout: _Layout, endpoint: str) -> None:
    """Send every service call of the AWS provider to ``endpoint`` (an emulator such as Floci)."""
    provider.update({
        "access_key": "floci",
        "secret_key": "floci",
        "s3_use_path_style": True,
        "skip_credentials_validation": True,
        "skip_metadata_api_check": True,
        "skip_region_validation": True,
        "skip_requesting_account_id": True,
        "endpoints": {
            "apigatewayv2": endpoint,
            "iam": endpoint,
            "lambda": endpoint,
            "s3": endpoint,
            "sqs": endpoint,
            "sts": endpoint,
        },
    })
    for kind in sorted({resource["kind"] for resource in layout.resources.values()}):
        if kind in KIND_PROVIDER_ENDPOINTS:
            provider["endpoints"][KIND_PROVIDER_ENDPOINTS[kind]] = endpoint


def write_terraform(
    specification: dict[str, Any],
    output: str | Path,
    **kwargs: Any,
) -> None:
    """Generate first, then write atomically; a failure never touches ``output``."""
    text = json.dumps(generate_terraform(specification, **kwargs), indent=2, sort_keys=True) + "\n"
    output = Path(output)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
