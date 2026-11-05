"""Lambda entry point shared by every component of an application.

The deployment sets ``PYIFC_COMPONENT`` (the component id) and ``PYIFC_APP_MODULE`` (the
application module, a dotted name or a file path) in each function's environment.
"""

from __future__ import annotations

import os
from typing import Any

from pyifc.core.models import ComponentKind
from pyifc.runtime import aws_adapters
from pyifc.runtime.loader import find_registry, load_app_module, resolve_function


def handler(event: dict[str, Any], context: object) -> Any:
    component_id = _required_environment("PYIFC_COMPONENT")
    module = load_app_module(_required_environment("PYIFC_APP_MODULE"))
    specification = find_registry(module).to_specification()

    component = next((item for item in specification.components if item.id == component_id), None)
    if component is None:
        raise RuntimeError(
            f"Unknown component '{component_id}' in application module '{module.__name__}'"
        )

    declared = {
        capability.resource_id
        for capability in specification.capabilities
        if capability.component_id == component_id
    }
    adapters = aws_adapters.bind_from_environment([
        resource for resource in specification.resources if resource.id in declared
    ])
    function = resolve_function(module, component.callable_ref)

    if component.kind == ComponentKind.HTTP_HANDLER:
        if component.properties.get("method") == "GET":
            return aws_adapters.handle_get(function, event, adapters)
        return aws_adapters.handle_post(function, event, adapters)
    if component.kind == ComponentKind.WORKER:
        return aws_adapters.handle_worker_event(function, event, adapters)
    if component.kind == ComponentKind.SCHEDULED_JOB:
        return aws_adapters.handle_schedule(function, event, adapters)
    raise RuntimeError(f"Component '{component_id}' has unsupported kind '{component.kind}'")


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value
