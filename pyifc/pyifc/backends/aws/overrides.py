"""Validation of per-resource ``aws`` overrides against an allowlist. Overrides never touch IAM."""

from __future__ import annotations

import re
from typing import Any

from pyifc.backends.aws.mapping import (
    LAMBDA_TIMEOUT_SECONDS,
    SUPPORTED_OVERRIDES,
    SUPPORTED_WORKER_TRIGGER_PREFIX,
)
from pyifc.core.models import (
    OVERRIDE_TARGETS,
)

_OVERRIDE_INTEGER_RANGES = {
    "visibility_timeout_seconds": (0, 43200),
    "message_retention_seconds": (60, 1209600),
    "delay_seconds": (0, 900),
    "receive_wait_time_seconds": (0, 20),
    "max_message_size": (1024, 1048576),
}


# Checked before the allowlist so an IAM attempt gets an explicit message.
_IAM_ATTRIBUTE = re.compile(r"iam|polic|role|permission|grant|acl|principal|statement|assume", re.IGNORECASE)


def override_problems(
    specification: dict[str, Any],
    resource_overrides: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Return every reason the "aws" overrides are not acceptable.

    Covers overrides declared on resources in the specification and those
    passed to generate_terraform(resource_overrides=...). Only attributes in
    SUPPORTED_OVERRIDES are accepted; anything that looks IAM-related is
    called out explicitly. Other targets' entries are not this backend's concern.
    """
    problems: list[str] = []
    resources = {resource["id"]: resource for resource in specification["resources"]}
    consumed = {
        str(component.get("trigger", ""))[len(SUPPORTED_WORKER_TRIGGER_PREFIX):]
        for component in specification["components"]
        if str(component.get("trigger", "")).startswith(SUPPORTED_WORKER_TRIGGER_PREFIX)
    }
    for resource in specification["resources"]:
        for target in resource.get("overrides", {}):
            if target not in OVERRIDE_TARGETS:
                problems.append(
                    f"resource '{resource['id']}': unknown override target '{target}' "
                    f"(known targets: {list(OVERRIDE_TARGETS)})"
                )
        declared = resource.get("overrides", {}).get("aws")
        if declared is not None:
            problems.extend(_check_overrides(resource, declared, "declared", consumed))
    for resource_id, attributes in (resource_overrides or {}).items():
        resource = resources.get(resource_id)
        if resource is None:
            problems.append(f"override for unknown resource '{resource_id}'")
        else:
            problems.extend(_check_overrides(resource, attributes, "passed", consumed))
    return problems


def _check_overrides(
    resource: dict[str, Any], attributes: Any, origin: str, consumed: set[str]
) -> list[str]:
    prefix = f"resource '{resource['id']}': {origin} override"
    if not isinstance(attributes, dict):
        return [f"{prefix}s must be a dict of attribute: value"]
    allowed = SUPPORTED_OVERRIDES.get(resource["kind"])
    if allowed is None:  # kind already rejected by unsupported_problems()
        return []
    problems: list[str] = []
    for name, value in sorted(attributes.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str):
            problems.append(f"{prefix} attribute {name!r} is not a string")
        elif _IAM_ATTRIBUTE.search(name):
            problems.append(
                f"{prefix} '{name}' touches IAM/permissions, which overrides may never change; "
                "permissions come only from declared capabilities"
            )
        elif name not in allowed:
            problems.append(
                f"{prefix} '{name}' is not supported for {resource['kind']} "
                f"(supported: {sorted(allowed)})"
            )
        else:
            problem = _override_value_problem(name, value)
            if problem:
                problems.append(f"{prefix} '{name}' {problem}")
            elif (
                name == "visibility_timeout_seconds"
                and resource["id"] in consumed
                and value < LAMBDA_TIMEOUT_SECONDS
            ):
                problems.append(
                    f"{prefix} '{name}' is below the {LAMBDA_TIMEOUT_SECONDS}s timeout of the "
                    "worker that consumes this queue; AWS would reject the event source mapping"
                )
    return problems


def _override_value_problem(name: str, value: Any) -> str | None:
    if name == "tags":
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and key and isinstance(item, str) for key, item in value.items()
        ):
            return "must be a dict of non-empty string keys to string values"
        if any(key.lower().startswith("aws:") for key in value):
            return "must not use the reserved 'aws:' tag prefix"
        if any("${" in key or "${" in item for key, item in value.items()):
            return "must not contain Terraform interpolation"
    elif name == "versioning":
        if not isinstance(value, bool):
            return "must be true or false"
    else:
        low, high = _OVERRIDE_INTEGER_RANGES[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return f"must be an integer between {low} and {high}"
    return None
