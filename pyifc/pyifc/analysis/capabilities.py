"""Checks on the capability edges (declared against used) and on queue and topic wiring."""

from __future__ import annotations

from typing import Any

from pyifc.analysis.common import add_diagnostic_at
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    Capability,
    ResourceKind,
)


def validate_capabilities(
    resources: dict[str, dict[str, Any]],
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]],
    inferred: dict[tuple[str, str, Capability], tuple[str, int, int]],
    components: list[dict[str, Any]],
    diagnostics: list[Diagnostic],
    filename: str,
) -> None:
    trigger_subscriptions = trigger_pairs(components)
    for key in sorted(declared.keys() | inferred.keys(), key=lambda item: (item[0], item[1], item[2].value)):
        _, resource_id, operation = key
        resource = next(item for item in resources.values() if item["id"] == resource_id)
        allowed_by_kind = {
            ResourceKind.OBJECT_STORE.value: {Capability.READ, Capability.WRITE, Capability.LIST, Capability.DELETE},
            ResourceKind.KEY_VALUE_STORE.value: {Capability.READ, Capability.WRITE, Capability.DELETE},
            ResourceKind.QUEUE.value: {Capability.PUBLISH, Capability.SUBSCRIBE},
            ResourceKind.TOPIC.value: {Capability.PUBLISH, Capability.SUBSCRIBE},
            ResourceKind.SECRET.value: {Capability.READ},
            ResourceKind.SCHEDULE.value: set(),
        }
        allowed = allowed_by_kind[resource["kind"]]
        location = inferred.get(key) or declared.get(key)
        if operation not in allowed:
            add_diagnostic_at(diagnostics, "E_INVALID_CAPABILITY", "error", f"Capability '{operation.value}' is invalid for {resource['kind']} resource '{resource_id}'.", filename, location)
        elif key in inferred and key not in declared:
            add_diagnostic_at(diagnostics, "E_UNDECLARED_USE", "error", f"Use of '{operation.value}' on '{resource_id}' is not declared for this component.", filename, inferred[key])
        elif (
            key in declared
            and key not in inferred
            and not (operation == Capability.SUBSCRIBE and (key[0], resource_id) in trigger_subscriptions)
        ):
            add_diagnostic_at(diagnostics, "W_UNUSED_CAPABILITY", "warning", f"Declared '{operation.value}' capability for '{resource_id}' has no statically visible use.", filename, declared[key])


def trigger_pairs(components: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """(component id, resource id) pairs where the resource is the component's trigger."""
    return {
        (component["id"], component["trigger"].split(":", 1)[1])
        for component in components
        if component["trigger"] and component["trigger"].startswith(("queue:", "topic:"))
    }


def validate_queues(
    resources: dict[str, dict[str, Any]],
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    for resource in resources.values():
        if resource["kind"] not in {ResourceKind.QUEUE.value, ResourceKind.TOPIC.value}:
            continue
        queue_id = resource["id"]
        publishers = [key for key in declared if key[1] == queue_id and key[2] == Capability.PUBLISH]
        subscribers = [key for key in declared if key[1] == queue_id and key[2] == Capability.SUBSCRIBE]
        location = next((value for key, value in declared.items() if key[1] == queue_id), None)
        is_topic = resource["kind"] == ResourceKind.TOPIC.value
        label = "Topic" if is_topic else "Queue"
        if subscribers and not publishers:
            code = "E_TOPIC_MISSING_PUBLISHER" if is_topic else "E_QUEUE_MISSING_PUBLISHER"
            add_diagnostic_at(diagnostics, code, "error", f"{label} '{queue_id}' has a subscriber but no declared publisher.", filename, location)
        if not is_topic and len(subscribers) > 1:
            add_diagnostic_at(diagnostics, "E_QUEUE_MULTIPLE_SUBSCRIBERS", "error", f"Point-to-point queue '{queue_id}' has multiple subscribers.", filename, location)
        if publishers and not subscribers:
            add_diagnostic_at(diagnostics, "W_QUEUE_NO_SUBSCRIBER", "warning", f"{label} '{queue_id}' has a publisher but no declared subscriber.", filename, location)
