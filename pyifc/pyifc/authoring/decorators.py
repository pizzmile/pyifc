from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from pyifc.authoring.registry import ApplicationRegistry
from pyifc.authoring.resources import Resource
from pyifc.core.models import (
    Capability,
    CapabilityDeclaration,
    ComponentDeclaration,
    ComponentKind,
    SourceLocation,
)

F = TypeVar("F", bound=Callable[..., Any])

_COMPONENT_ID_ATTRIBUTE = "__pyifc_component_id__"


def handler(
    registry: ApplicationRegistry,
    *,
    method: str,
    path: str,
    component_id: str | None = None,
) -> Callable[[F], F]:
    """Declare a function as an HTTP handler."""

    normalized_method = method.strip().upper()

    if not normalized_method:
        raise ValueError("HTTP method cannot be empty.")

    if not path.startswith("/"):
        raise ValueError("HTTP handler path must start with '/'.")

    def decorator(function: F) -> F:
        previous_component_id = get_registered_component_id(function)
        resolved_component_id = (
            component_id
            or previous_component_id
            or default_component_id(function)
        )

        if previous_component_id and previous_component_id != resolved_component_id:
            registry.rename_capability_component(
                previous_component_id,
                resolved_component_id,
            )

        set_registered_component_id(function, resolved_component_id)

        registry.register_component(
            ComponentDeclaration(
                id=resolved_component_id,
                kind=ComponentKind.HTTP_HANDLER,
                callable_ref=callable_reference(function),
                properties={
                    "method": normalized_method,
                    "path": path,
                },
                source=source_location(function),
            )
        )

        if os.environ.get("PYIFC_TARGET") == "aws":
            from pyifc.runtime.aws_adapters import (
                bind_from_environment,
                handle_get,
                handle_post,
            )

            @wraps(function)
            def aws_handler(event: dict, context: Any) -> dict:
                specification = registry.to_specification()
                resource_ids = {
                    capability.resource_id
                    for capability in specification.capabilities
                    if capability.component_id == resolved_component_id
                }
                adapters = bind_from_environment([
                    resource for resource in specification.resources
                    if resource.id in resource_ids
                ])
                if normalized_method == "GET":
                    return handle_get(function, event, adapters)
                return handle_post(function, event, adapters)

            return aws_handler  # type: ignore[return-value]
        return function

    return decorator


def worker(
    registry: ApplicationRegistry,
    *,
    component_id: str | None = None,
) -> Callable[[F], F]:
    """Declare a function as a background worker."""

    def decorator(function: F) -> F:
        previous_component_id = get_registered_component_id(function)
        resolved_component_id = (
            component_id
            or previous_component_id
            or default_component_id(function)
        )

        if previous_component_id and previous_component_id != resolved_component_id:
            registry.rename_capability_component(
                previous_component_id,
                resolved_component_id,
            )

        set_registered_component_id(function, resolved_component_id)

        registry.register_component(
            ComponentDeclaration(
                id=resolved_component_id,
                kind=ComponentKind.WORKER,
                callable_ref=callable_reference(function),
                properties={},
                source=source_location(function),
            )
        )

        if os.environ.get("PYIFC_TARGET") == "aws":
            from pyifc.runtime.aws_adapters import (
                bind_from_environment,
                handle_worker_event,
            )

            @wraps(function)
            def aws_worker(event: dict, context: Any) -> Any:
                specification = registry.to_specification()
                resource_ids = {
                    capability.resource_id
                    for capability in specification.capabilities
                    if capability.component_id == resolved_component_id
                }
                return handle_worker_event(
                    function,
                    event,
                    bind_from_environment([
                        resource for resource in specification.resources
                        if resource.id in resource_ids
                    ]),
                )

            return aws_worker  # type: ignore[return-value]

        return function

    return decorator


def scheduled(
    registry: ApplicationRegistry,
    schedule: Resource,
    *,
    component_id: str | None = None,
) -> Callable[[F], F]:
    registry.register_resource(schedule.to_declaration())

    def decorator(function: F) -> F:
        previous_component_id = get_registered_component_id(function)
        resolved_component_id = component_id or previous_component_id or default_component_id(function)
        if previous_component_id and previous_component_id != resolved_component_id:
            registry.rename_capability_component(previous_component_id, resolved_component_id)
        set_registered_component_id(function, resolved_component_id)
        registry.register_component(
            ComponentDeclaration(
                id=resolved_component_id,
                kind=ComponentKind.SCHEDULED_JOB,
                callable_ref=callable_reference(function),
                properties={},
                source=source_location(function),
                trigger=f"schedule:{schedule.logical_name}",
            )
        )

        if os.environ.get("PYIFC_TARGET") == "aws":
            from pyifc.runtime.aws_adapters import (
                bind_from_environment,
                handle_schedule,
            )

            @wraps(function)
            def aws_scheduled(event: dict, context: Any) -> Any:
                specification = registry.to_specification()
                resource_ids = {
                    capability.resource_id
                    for capability in specification.capabilities
                    if capability.component_id == resolved_component_id
                }
                return handle_schedule(
                    function,
                    event,
                    bind_from_environment([
                        resource for resource in specification.resources
                        if resource.id in resource_ids
                    ]),
                )

            return aws_scheduled  # type: ignore[return-value]
        return function

    return decorator


def capability(
    registry: ApplicationRegistry,
    *,
    resource: Resource,
    operation: Capability,
) -> Callable[[F], F]:
    """Declare that a component uses a resource with a capability."""

    registry.register_resource(resource.to_declaration())

    def decorator(function: F) -> F:
        resolved_component_id = (
            get_registered_component_id(function)
            or default_component_id(function)
        )

        set_registered_component_id(function, resolved_component_id)

        registry.register_capability(
            CapabilityDeclaration(
                component_id=resolved_component_id,
                resource_id=resource.logical_name,
                operation=operation,
                source=source_location(function),
            )
        )

        return function

    return decorator


def read(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.READ,
    )


def write(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.WRITE,
    )


def list(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.LIST,
    )


def delete(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.DELETE,
    )


def publish(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.PUBLISH,
    )


def subscribe(
    registry: ApplicationRegistry,
    resource: Resource,
) -> Callable[[F], F]:
    return capability(
        registry,
        resource=resource,
        operation=Capability.SUBSCRIBE,
    )


def default_component_id(function: Callable[..., Any]) -> str:
    """Create a globally unique default ID from module and function names."""

    return f"{function.__module__}.{function.__qualname__}"


def callable_reference(function: Callable[..., Any]) -> str:
    """Return a reference that can later be used by the runtime loader."""

    return f"{function.__module__}:{function.__qualname__}"


def source_location(
    function: Callable[..., Any],
) -> SourceLocation | None:
    """Find the source file and starting line for a function."""

    try:
        file_path = inspect.getsourcefile(function)
        _, line_number = inspect.getsourcelines(function)
    except (OSError, TypeError):
        return None

    if file_path is None:
        return None

    return SourceLocation(
        file=file_path,
        line=line_number,
    )


def get_registered_component_id(
    function: Callable[..., Any],
) -> str | None:
    return getattr(function, _COMPONENT_ID_ATTRIBUTE, None)


def set_registered_component_id(
    function: Callable[..., Any],
    component_id: str,
) -> None:
    setattr(function, _COMPONENT_ID_ATTRIBUTE, component_id)