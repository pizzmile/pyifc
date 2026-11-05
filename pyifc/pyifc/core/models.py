from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ResourceKind(StrEnum):
    OBJECT_STORE = "object_store"
    KEY_VALUE_STORE = "key_value_store"
    QUEUE = "queue"
    TOPIC = "topic"
    SCHEDULE = "schedule"
    SECRET = "secret"


# Single definition of which kinds hold data that must survive a redeploy. Used
# by the analyzer (the checked specification's ``stateful`` flag) and by the
# runtime declarations in resources.py so the two cannot disagree.
STATEFUL_KINDS = frozenset({
    ResourceKind.OBJECT_STORE,
    ResourceKind.KEY_VALUE_STORE,
    ResourceKind.SECRET,
})


class ComponentKind(StrEnum):
    HTTP_HANDLER = "http_handler"
    WORKER = "worker"
    SCHEDULED_JOB = "scheduled_job"


class Capability(StrEnum):
    READ = "read"
    WRITE = "write"
    LIST = "list"
    DELETE = "delete"
    PUBLISH = "publish"
    SUBSCRIBE = "subscribe"


# (resource kind value, method name) -> capability the method exercises.
METHOD_CAPABILITY: dict[tuple[str, str], Capability] = {
    ("object_store", "get"): Capability.READ,
    ("object_store", "put"): Capability.WRITE,
    ("object_store", "list"): Capability.LIST,
    ("object_store", "delete"): Capability.DELETE,
    ("key_value_store", "get"): Capability.READ,
    ("key_value_store", "put"): Capability.WRITE,
    ("key_value_store", "delete"): Capability.DELETE,
    ("queue", "publish"): Capability.PUBLISH,
    ("queue", "receive"): Capability.SUBSCRIBE,
    ("topic", "publish"): Capability.PUBLISH,
    ("topic", "receive"): Capability.SUBSCRIBE,
    ("secret", "get"): Capability.READ,
}


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int
    column: int = 0


@dataclass(frozen=True)
class ResourceDeclaration:
    id: str
    kind: ResourceKind
    properties: dict[str, Any] = field(default_factory=dict)
    stateful: bool = False
    # Target-specific setting overrides, {"aws": {"tags": {...}}}. Only the
    # shape is checked here; each backend validates its own target's entries.
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Former logical id, set by the developer to declare a rename explicitly.
    was: str | None = None
    # Sorted "<component id>><capability>" entries of the edges the analyzer saw
    # used (provenance "both" or "used"). Source positions never enter it.
    use_signature: list[str] = field(default_factory=list)


OVERRIDE_TARGETS = ("aws",)  # backends that exist; anything else is a typo


def check_overrides_shape(overrides: Any) -> dict[str, dict[str, Any]]:
    """Return ``overrides`` if it is ``{target: {attribute: value}}``, else raise ValueError."""
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be a dict of {target: {attribute: value}}.")
    for target, attributes in overrides.items():
        if not isinstance(target, str) or not isinstance(attributes, dict):
            raise ValueError("overrides must be a dict of {target: {attribute: value}}.")
        if target not in OVERRIDE_TARGETS:
            raise ValueError(f"unknown override target '{target}' (known targets: {list(OVERRIDE_TARGETS)}).")
        if not all(isinstance(name, str) for name in attributes):
            raise ValueError(f"overrides for target '{target}' must have string attribute names.")
        if not _is_json_value(attributes):
            raise ValueError(f"overrides for target '{target}' must contain only JSON values.")
    return overrides


def _is_json_value(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    return value is None or isinstance(value, (str, int, float, bool))


@dataclass
class ComponentDeclaration:
    id: str
    kind: ComponentKind
    callable_ref: str
    properties: dict[str, Any] = field(default_factory=dict)
    source: SourceLocation | None = None
    trigger: str | None = None


@dataclass(frozen=True)
class CapabilityDeclaration:
    component_id: str
    resource_id: str
    operation: Capability
    source: SourceLocation | None = None
    # declared | used | both | implied (see the analyzer and iam_mode).
    provenance: str = "declared"


@dataclass
class ApplicationSpecification:
    name: str
    resources: list[ResourceDeclaration]
    components: list[ComponentDeclaration]
    capabilities: list[CapabilityDeclaration]
    schema_version: str = "1.3"

    def to_dict(self) -> dict[str, Any]:
        def normalize(value: Any) -> Any:
            if isinstance(value, StrEnum):
                return value.value
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        data = normalize(asdict(self))
        # Omitted when empty or unset so specifications without them stay small.
        for resource in data["resources"]:
            if not resource["overrides"]:
                del resource["overrides"]
            if resource["was"] is None:
                del resource["was"]
        return data