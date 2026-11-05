from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyifc.core.binding import adapter
from pyifc.core.models import (
    STATEFUL_KINDS,
    ResourceDeclaration,
    ResourceKind,
    check_overrides_shape,
)


@dataclass(frozen=True)
class Resource:
    logical_name: str
    kind: ResourceKind
    properties: dict[str, Any]
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    was: str | None = None

    def __post_init__(self) -> None:
        check_overrides_shape(self.overrides)
        if self.was is not None:
            if not isinstance(self.was, str) or not self.was.strip():
                raise ValueError("was must be a non-empty string naming the former resource id.")
            if self.was == self.logical_name:
                raise ValueError(f"was equals the resource's own id '{self.logical_name}'.")

    def to_declaration(self) -> ResourceDeclaration:
        return ResourceDeclaration(
            id=self.logical_name,
            kind=self.kind,
            properties=self.properties,
            stateful=self.kind in STATEFUL_KINDS,
            overrides=self.overrides,
            was=self.was,
        )


class Bucket(Resource):
    def __init__(
        self,
        logical_name: str,
        *,
        overrides: dict[str, dict[str, Any]] | None = None,
        was: str | None = None,
    ) -> None:
        super().__init__(
            logical_name=logical_name,
            kind=ResourceKind.OBJECT_STORE,
            properties={},
            overrides=overrides or {},
            was=was,
        )

    def get(self, key: str) -> tuple[bytes, str]:
        return adapter(self.logical_name).get(key)

    def put(self, key: str, value: bytes) -> None:
        adapter(self.logical_name).put(key, value, "application/json")

    def list(self, prefix: str = "") -> list[str]:
        return adapter(self.logical_name).list(prefix)

    def delete(self, key: str) -> None:
        raise NotImplementedError("Bucket operations require a runtime adapter.")


class Queue(Resource):
    def __init__(
        self,
        logical_name: str,
        *,
        overrides: dict[str, dict[str, Any]] | None = None,
        was: str | None = None,
    ) -> None:
        super().__init__(
            logical_name=logical_name,
            kind=ResourceKind.QUEUE,
            properties={},
            overrides=overrides or {},
            was=was,
        )

    def publish(self, message: bytes) -> None:
        adapter(self.logical_name).publish(message)

    def receive(self) -> bytes:
        raise NotImplementedError("Queue operations require a runtime adapter.")


class KeyValueStore(Resource):
    def __init__(
        self,
        logical_name: str,
        *,
        overrides: dict[str, dict[str, Any]] | None = None,
        was: str | None = None,
    ) -> None:
        super().__init__(logical_name, ResourceKind.KEY_VALUE_STORE, {}, overrides or {}, was)

    def get(self, key: str) -> bytes:
        """Return the stored value; raises KeyError when the key is absent."""
        return adapter(self.logical_name).get(key)

    def put(self, key: str, value: bytes) -> None:
        adapter(self.logical_name).put(key, value)

    def delete(self, key: str) -> None:
        adapter(self.logical_name).delete(key)


class Topic(Resource):
    def __init__(
        self,
        logical_name: str,
        *,
        overrides: dict[str, dict[str, Any]] | None = None,
        was: str | None = None,
    ) -> None:
        super().__init__(logical_name, ResourceKind.TOPIC, {}, overrides or {}, was)

    def publish(self, message: bytes) -> None:
        adapter(self.logical_name).publish(message)

    def receive(self) -> bytes:
        raise NotImplementedError("Topic operations require a runtime adapter.")


class Schedule(Resource):
    def __init__(self, logical_name: str, *, cron: str, was: str | None = None) -> None:
        if not cron.strip():
            raise ValueError("Schedule cron expression cannot be empty.")
        super().__init__(logical_name, ResourceKind.SCHEDULE, {"cron": cron}, was=was)


class Secret(Resource):
    def __init__(
        self,
        logical_name: str,
        *,
        overrides: dict[str, dict[str, Any]] | None = None,
        was: str | None = None,
    ) -> None:
        super().__init__(logical_name, ResourceKind.SECRET, {}, overrides or {}, was)

    def get(self) -> str:
        return adapter(self.logical_name).get()
