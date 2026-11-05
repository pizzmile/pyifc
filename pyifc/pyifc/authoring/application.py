from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pyifc.authoring import decorators
from pyifc.authoring.registry import ApplicationRegistry
from pyifc.authoring.resources import Resource

F = TypeVar("F", bound=Callable[..., Any])


class Application:
    def __init__(self, name: str) -> None:
        if not name.strip():
            raise ValueError("Application name cannot be empty.")

        self.name = name
        self.registry = ApplicationRegistry(name)

    def handler(
        self,
        *,
        method: str,
        path: str,
        id: str | None = None,
    ) -> Callable[[F], F]:
        return decorators.handler(
            self.registry,
            method=method,
            path=path,
            component_id=id,
        )

    def worker(
        self,
        *,
        id: str | None = None,
    ) -> Callable[[F], F]:
        return decorators.worker(
            self.registry,
            component_id=id,
        )

    def event_handler(
        self,
        *,
        id: str | None = None,
    ) -> Callable[[F], F]:
        return self.worker(id=id)

    def scheduled(
        self,
        schedule: Resource,
        *,
        id: str | None = None,
    ) -> Callable[[F], F]:
        return decorators.scheduled(
            self.registry,
            schedule,
            component_id=id,
        )

    def read(self, resource: Resource) -> Callable[[F], F]:
        return decorators.read(self.registry, resource)

    def write(self, resource: Resource) -> Callable[[F], F]:
        return decorators.write(self.registry, resource)

    def list(self, resource: Resource) -> Callable[[F], F]:
        return decorators.list(self.registry, resource)

    def delete(self, resource: Resource) -> Callable[[F], F]:
        return decorators.delete(self.registry, resource)

    def publish(self, resource: Resource) -> Callable[[F], F]:
        return decorators.publish(self.registry, resource)

    def subscribe(self, resource: Resource) -> Callable[[F], F]:
        return decorators.subscribe(self.registry, resource)

    def to_spec(self) -> dict[str, Any]:
        return self.registry.to_specification().to_dict()