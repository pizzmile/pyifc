"""Run an application on the local machine, with the same capability checks as a deployment.

Buckets, key-value stores and secrets live as files under a state directory; topics and
queues are in memory. Each component gets only the adapters of the resources it declared,
wrapped so that it can call only the methods its declared capabilities allow.
"""

from __future__ import annotations

import base64
import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pyifc.core.models import ComponentDeclaration, ComponentKind, ResourceKind
from pyifc.runtime import aws_adapters
from pyifc.runtime.enforce import restrict
from pyifc.runtime.loader import find_registry, load_app_module, resolve_function


class LocalBucket:
    def __init__(self, root: Path, content_types: Path) -> None:
        self.root = root.resolve()
        self.content_types = content_types
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not key or self.root not in path.parents:
            raise ValueError(f"Invalid object key {key!r}")
        return path

    def _types(self) -> dict[str, str]:
        if not self.content_types.exists():
            return {}
        return json.loads(self.content_types.read_text())

    def put(self, key: str, body: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        types = self._types()
        types[key] = content_type
        self.content_types.parent.mkdir(parents=True, exist_ok=True)
        self.content_types.write_text(json.dumps(types, sort_keys=True))

    def get(self, key: str) -> tuple[bytes, str]:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes(), self._types().get(key, "")

    def list(self, prefix: str = "") -> list[str]:
        keys = (
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        )
        return sorted(key for key in keys if key.startswith(prefix))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        types = self._types()
        if types.pop(key, None) is not None:
            self.content_types.write_text(json.dumps(types, sort_keys=True))


class LocalTable:
    """Key-value store; each value is a file named by the hex of its key."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key:
            raise ValueError("Empty key")
        return self.root / key.encode("utf-8").hex()

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()

    def put(self, key: str, value: bytes) -> None:
        self._path(key).write_bytes(value)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class LocalSecret:
    def __init__(self, path: Path, secret_id: str) -> None:
        self.path = path
        self.secret_id = secret_id

    def get(self) -> str:
        if not self.path.is_file():
            raise KeyError(f"Secret '{self.secret_id}' has no value in {self.path}")
        return self.path.read_text()


class LocalMessages:
    """In-memory topic or queue; ``LocalApp.deliver`` drains it."""

    def __init__(self) -> None:
        self.pending: deque[bytes] = deque()

    def publish(self, message: bytes) -> None:
        self.pending.append(bytes(message))


class LocalApp:
    def __init__(self, app_module: Path, state_dir: Path, *, enforce: bool = True) -> None:
        self.enforce = enforce
        self.state_dir = Path(state_dir)
        self.module = load_app_module(str(app_module))
        self.specification = find_registry(self.module).to_specification()
        self._kinds = {resource.id: resource.kind for resource in self.specification.resources}
        self._components = {component.id: component for component in self.specification.components}
        self._adapters = {
            resource.id: adapter
            for resource in self.specification.resources
            if (adapter := self._make_adapter(resource.id, resource.kind)) is not None
        }

    def call_http(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Route a request to the component whose trigger is ``http:<METHOD> <path>``.

        A ``CapabilityError`` raised by the component propagates; it is not turned into a 500.
        """
        parts = urlsplit(path)
        component = self._route(method.upper(), parts.path)
        if component is None:
            message = json.dumps({"error": {"code": "not_found", "message": "No route"}})
            return 404, message.encode("utf-8")
        query = dict(parse_qsl(parts.query))
        event = {
            "body": base64.b64encode(body).decode("ascii"),
            "isBase64Encoded": True,
            "headers": dict(headers or {}),
            "queryStringParameters": query or None,
        }
        function = resolve_function(self.module, component.callable_ref)
        adapters = self._adapters_for(component)
        if method.upper() == "GET":
            response = aws_adapters.handle_get(function, event, adapters)
        else:
            response = aws_adapters.handle_post(function, event, adapters)
        return response["statusCode"], response["body"].encode("utf-8")

    def deliver(self) -> int:
        """Invoke subscribers for queued messages until none remain; return how many were delivered.

        A topic message goes to every subscriber, a queue message to the first one (by id).
        Messages on a resource with no subscriber stay queued.
        """
        delivered = 0
        progressed = True
        while progressed:
            progressed = False
            for resource_id in sorted(self._adapters):
                messages = self._adapters[resource_id]
                if not isinstance(messages, LocalMessages) or not messages.pending:
                    continue
                kind = self._kinds[resource_id]
                subscribers = self._subscribers(resource_id, kind)
                if not subscribers:
                    continue
                message = messages.pending.popleft()
                for component in subscribers if kind == ResourceKind.TOPIC else subscribers[:1]:
                    self._invoke_worker(component, kind, message)
                delivered += 1
                progressed = True
        return delivered

    def _route(self, method: str, path: str) -> ComponentDeclaration | None:
        for component in self.specification.components:
            if component.kind != ComponentKind.HTTP_HANDLER:
                continue
            trigger = component.trigger or (
                f"http:{component.properties.get('method')} {component.properties.get('path')}"
            )
            if trigger == f"http:{method} {path}":
                return component
        return None

    def _subscribers(self, resource_id: str, kind: ResourceKind) -> list[ComponentDeclaration]:
        trigger = f"{'topic' if kind == ResourceKind.TOPIC else 'queue'}:{resource_id}"
        return sorted(
            (c for c in self.specification.components if c.trigger == trigger),
            key=lambda component: component.id,
        )

    def _invoke_worker(
        self, component: ComponentDeclaration, kind: ResourceKind, message: bytes
    ) -> None:
        text = message.decode("utf-8")
        record = {"Sns": {"Message": text}} if kind == ResourceKind.TOPIC else {"body": text}
        function = resolve_function(self.module, component.callable_ref)
        aws_adapters.handle_worker_event(
            function, {"Records": [record]}, self._adapters_for(component)
        )

    def _adapters_for(self, component: ComponentDeclaration) -> dict[str, Any]:
        allowed: dict[str, set[str]] = {}
        for capability in self.specification.capabilities:
            if capability.component_id == component.id:
                allowed.setdefault(capability.resource_id, set()).add(capability.operation.value)
        adapters: dict[str, Any] = {}
        for resource_id, operations in sorted(allowed.items()):
            adapter = self._adapters.get(resource_id)
            if adapter is None:
                continue
            adapters[resource_id] = (
                restrict(
                    adapter,
                    self._kinds[resource_id].value,
                    frozenset(operations),
                    component_id=component.id,
                )
                if self.enforce
                else adapter
            )
        return adapters

    def _make_adapter(self, resource_id: str, kind: ResourceKind) -> Any:
        state = self.state_dir
        if kind == ResourceKind.OBJECT_STORE:
            return LocalBucket(
                state / "buckets" / resource_id, state / "content-types" / f"{resource_id}.json"
            )
        if kind == ResourceKind.KEY_VALUE_STORE:
            return LocalTable(state / "tables" / resource_id)
        if kind == ResourceKind.SECRET:
            return LocalSecret(state / "secrets" / resource_id, resource_id)
        if kind in (ResourceKind.QUEUE, ResourceKind.TOPIC):
            return LocalMessages()
        return None
