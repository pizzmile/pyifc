"""Components: the Application variable, decorated functions, and what may surround them."""

from __future__ import annotations

import ast
from typing import Any

from pyifc.analysis.common import (
    add_diagnostic,
    literal_string,
    location_dict,
    node_location,
)
from pyifc.analysis.imports import is_foreign_resource_call
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    Capability,
    ComponentKind,
    ResourceKind,
)

_COMPONENT_DECORATORS = frozenset({"handler", "worker", "event_handler", "scheduled"})


_DECORATOR_CAPABILITIES = {
    "read": Capability.READ,
    "write": Capability.WRITE,
    "list": Capability.LIST,
    "delete": Capability.DELETE,
    "publish": Capability.PUBLISH,
    "subscribe": Capability.SUBSCRIBE,
}


def find_application(tree: ast.Module, imports: set[str]) -> tuple[str | None, str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not isinstance(node.value.func, ast.Name) or node.value.func.id not in imports:
            continue
        if node.value.func.id != "Application" or not node.value.args:
            continue
        if not isinstance(node.value.args[0], ast.Constant) or not isinstance(node.value.args[0].value, str):
            continue
        target = next((item.id for item in node.targets if isinstance(item, ast.Name)), None)
        if target:
            return target, node.value.args[0].value
    return None, ""


def parse_component(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    app_name: str | None,
    application_name: str,
    module_name: str,
    resources: dict[str, dict[str, Any]],
    filename: str,
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]],
    diagnostics: list[Diagnostic],
) -> dict[str, Any] | None:
    if app_name is None:
        return None
    component_kind: ComponentKind | None = None
    component_id: str | None = None
    properties: dict[str, Any] = {}
    trigger: str | None = None
    pending_capabilities: list[tuple[str, Capability, ast.AST]] = []

    component_decorators = [
        decorator
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == app_name
        and decorator.func.attr in _COMPONENT_DECORATORS
    ]
    for extra in component_decorators[1:]:
        add_diagnostic(
            diagnostics,
            "E_STACKED_COMPONENT_DECORATORS",
            "error",
            f"Function '{node.name}' carries more than one of handler, worker and scheduled; a function is one component.",
            filename,
            extra,
        )

    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != app_name:
            continue
        name = decorator.func.attr
        keywords = {item.arg: item.value for item in decorator.keywords if item.arg}
        if name == "handler":
            component_kind = ComponentKind.HTTP_HANDLER
            method = literal_string(keywords.get("method"))
            path = literal_string(keywords.get("path"))
            component_id = literal_string(keywords.get("id"))
            if method is None or not method.strip() or path is None or not path.startswith("/"):
                add_diagnostic(diagnostics, "E_DECORATOR_ARGUMENT", "error", "HTTP handler requires a non-empty literal method and a literal path starting with '/'.", filename, decorator)
                continue
            properties = {"method": method.upper(), "path": path}
            trigger = f"http:{method.upper()} {path}"
        elif name in {"worker", "event_handler"}:
            component_kind = ComponentKind.WORKER
            component_id = literal_string(keywords.get("id"))
        elif name == "scheduled":
            component_kind = ComponentKind.SCHEDULED_JOB
            component_id = literal_string(keywords.get("id"))
            if len(decorator.args) != 1 or not isinstance(decorator.args[0], ast.Name):
                add_diagnostic(diagnostics, "E_RESOURCE_REFERENCE", "error", "Scheduled handlers must reference a declared Schedule variable.", filename, decorator)
            else:
                schedule_name = decorator.args[0].id
                schedule = resources.get(schedule_name)
                if schedule is None or schedule["kind"] != ResourceKind.SCHEDULE.value:
                    add_diagnostic(diagnostics, "E_INVALID_TRIGGER", "error", f"'{schedule_name}' is not a declared schedule resource.", filename, decorator)
                else:
                    trigger = f"schedule:{schedule['id']}"
        elif name in _DECORATOR_CAPABILITIES:
            if len(decorator.args) != 1 or not isinstance(decorator.args[0], ast.Name):
                add_diagnostic(diagnostics, "E_RESOURCE_REFERENCE", "error", "Capability decorators must reference a declared resource variable directly.", filename, decorator)
                continue
            resource_variable = decorator.args[0].id
            if resource_variable not in resources:
                add_diagnostic(diagnostics, "E_UNKNOWN_RESOURCE", "error", f"Unknown resource '{resource_variable}'.", filename, decorator)
                continue
            pending_capabilities.append((resource_variable, _DECORATOR_CAPABILITIES[name], decorator))
        else:
            add_diagnostic(
                diagnostics,
                "E_UNSUPPORTED_DECORATOR",
                "error",
                f"Decorator 'app.{name}' is not supported.",
                filename,
                decorator,
            )

    if component_kind is None:
        if pending_capabilities:
            add_diagnostic(
                diagnostics,
                "E_COMPONENT_DECLARATION",
                "error",
                "Capabilities must decorate a handler or worker function.",
                filename,
                node,
            )
        return None
    if not component_id:
        component_id = f"{module_name}.{node.name}"
        add_diagnostic(
            diagnostics,
            "W_COMPONENT_DEFAULT_ID",
            "warning",
            f"Component '{node.name}' has no literal id= and defaults to '{component_id}'; renaming the function or module changes its identity.",
            filename,
            node,
        )
    for resource_variable, operation, source_node in pending_capabilities:
        key = (component_id, resources[resource_variable]["id"], operation)
        declared[key] = node_location(source_node)
        if operation == Capability.SUBSCRIBE and component_kind != ComponentKind.WORKER:
            add_diagnostic(
                diagnostics,
                "E_HANDLER_SUBSCRIBE",
                "error",
                f"Component '{component_id}' ({component_kind.value}) cannot subscribe to a queue or topic: the subscription would replace its own trigger. Use a worker.",
                filename,
                source_node,
            )
        elif operation == Capability.SUBSCRIBE:
            resource_kind = resources[resource_variable]["kind"]
            trigger_kind = "topic" if resource_kind == ResourceKind.TOPIC.value else "queue"
            trigger = f"{trigger_kind}:{resources[resource_variable]['id']}"

    return {
        "id": component_id,
        "kind": component_kind.value,
        "callable_ref": f"{module_name}:{node.name}",
        "properties": properties,
        "source": location_dict(filename, node_location(node)),
        "trigger": trigger,
    }


def validate_components(
    components: list[dict[str, Any]],
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    for component in components:
        if component["kind"] != ComponentKind.WORKER.value:
            continue
        subscriptions = [
            key
            for key in declared
            if key[0] == component["id"] and key[2] == Capability.SUBSCRIBE
        ]
        if len(subscriptions) != 1:
            location = component["source"]
            diagnostics.append(
                Diagnostic(
                    code="E_WORKER_TRIGGER",
                    severity="error",
                    message="A worker must subscribe to exactly one queue.",
                    file=filename,
                    line=location["line"],
                    column=location["column"],
                )
            )


def validate_helper_resource_uses(
    tree: ast.Module,
    component_nodes: set[ast.FunctionDef | ast.AsyncFunctionDef],
    resources: dict[str, dict[str, Any]],
    filename: str,
    diagnostics: list[Diagnostic],
    foreign_names: frozenset[str] = frozenset(),
) -> None:
    """Reject every mention of a resource outside decorated components.

    Undecorated functions, class bodies and module-level statements are not
    components, so a resource referenced there would escape the declaration
    check. A bare reference counts, not only a method call, because it can be
    stored, returned or passed on.
    """
    resource_names = set(resources)
    for node in tree.body:
        if node in component_nodes:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            code = "E_UNSUPPORTED_HELPER_RESOURCE_USE"
            message = "Resource operations in undecorated helper functions are unsupported in v1."
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        else:
            code = "E_UNSUPPORTED_MODULE_LEVEL_USE"
            message = "Resources may only be referenced inside decorated components."
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in resource_names and isinstance(child.ctx, ast.Load):
                add_diagnostic(diagnostics, code, "error", message, filename, child)
            elif isinstance(child, ast.Call) and is_foreign_resource_call(child, foreign_names):
                assert isinstance(child.func, ast.Attribute)
                add_diagnostic(
                    diagnostics,
                    "E_UNSUPPORTED_CROSS_MODULE_RESOURCE",
                    "error",
                    f"'{ast.unparse(child.func.value)}' comes from another module; resources must be declared in the analyzed module.",
                    filename,
                    child,
                )


def validate_component_signature(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    app_name: str | None,
    resources: dict[str, dict[str, Any]],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Every mention of a resource in a component is a direct call in its body or a declaration.

    The body walk (infer_function_uses) decides what a mention inside the body
    means. Anywhere else in the definition (default values, annotations, type
    parameters, decorators) a resource would reach the component with no
    capability attached, so any mention there is rejected, whatever the syntax.
    Only the direct argument of a pyifc capability or schedule decorator is the
    declaration and stays allowed.
    """
    resource_names = set(resources)
    in_body = {id(child) for statement in function.body for child in ast.walk(statement)}
    declaration_arguments: set[int] = set()
    for decorator in function.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == app_name
            and (decorator.func.attr in _DECORATOR_CAPABILITIES or decorator.func.attr == "scheduled")
        ):
            declaration_arguments.update(id(item) for item in decorator.args if isinstance(item, ast.Name))
    for child in ast.walk(function):
        if (
            isinstance(child, ast.Name)
            and child.id in resource_names
            and id(child) not in in_body
            and id(child) not in declaration_arguments
        ):
            add_diagnostic(
                diagnostics,
                "E_UNSUPPORTED_SIGNATURE_RESOURCE_USE",
                "error",
                f"Resource '{child.id}' appears outside the function body and outside a capability declaration.",
                filename,
                child,
            )


def validate_component_references(
    tree: ast.Module,
    component_nodes: set[ast.FunctionDef | ast.AsyncFunctionDef],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    """A component may not be named in code: calling one from another runs its uses under the caller's role."""
    names = {node.name for node in component_nodes}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load):
            add_diagnostic(
                diagnostics,
                "E_UNSUPPORTED_COMPONENT_CALL",
                "error",
                f"Component '{node.id}' is referenced in code; its operations would run under another component's role.",
                filename,
                node,
            )
