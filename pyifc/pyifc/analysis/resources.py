"""Resource declarations at module scope: discovery, placement, rebinding, use and naming checks."""

from __future__ import annotations

import ast
from typing import Any

from pyifc.analysis.common import add_diagnostic, literal_string
from pyifc.analysis.imports import plain_imported_modules
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    STATEFUL_KINDS,
    Capability,
    ResourceKind,
    check_overrides_shape,
)
from pyifc.core.naming import physical_suffix, terraform_name

_RESOURCE_CONSTRUCTORS = frozenset({"Bucket", "KeyValueStore", "Queue", "Topic", "Schedule", "Secret"})


def find_resources(
    tree: ast.Module,
    imports: set[str],
    filename: str,
    diagnostics: list[Diagnostic],
) -> tuple[dict[str, dict[str, Any]], dict[str, ast.AST]]:
    resources: dict[str, dict[str, Any]] = {}
    resource_nodes: dict[str, ast.AST] = {}
    kinds = {
        "Bucket": ResourceKind.OBJECT_STORE,
        "KeyValueStore": ResourceKind.KEY_VALUE_STORE,
        "Queue": ResourceKind.QUEUE,
        "Topic": ResourceKind.TOPIC,
        "Schedule": ResourceKind.SCHEDULE,
        "Secret": ResourceKind.SECRET,
    }
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id not in imports or call.func.id not in kinds:
            continue
        target_names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if len(target_names) != 1 or not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            add_diagnostic(diagnostics, "E_RESOURCE_DECLARATION", "error", "Resources must be assigned once using a literal logical name.", filename, node)
            continue
        properties: dict[str, Any] = {}
        if call.func.id == "Schedule":
            cron_node = next((item.value for item in call.keywords if item.arg == "cron"), None)
            cron = literal_string(cron_node)
            if cron is None or not cron.strip():
                add_diagnostic(diagnostics, "E_RESOURCE_DECLARATION", "error", "Schedule requires a non-empty literal cron expression.", filename, node)
                continue
            properties["cron"] = cron
        overrides: dict[str, Any] = {}
        overrides_node = next((item.value for item in call.keywords if item.arg == "overrides"), None)
        if overrides_node is not None:
            try:
                if call.func.id not in {"Bucket", "Queue", "KeyValueStore", "Topic", "Secret"}:
                    raise ValueError("Only Bucket, Queue, KeyValueStore, Topic and Secret accept overrides.")
                overrides = check_overrides_shape(ast.literal_eval(overrides_node))
            except (ValueError, TypeError, SyntaxError) as error:
                add_diagnostic(diagnostics, "E_RESOURCE_DECLARATION", "error", f"Invalid overrides: {error}", filename, node)
                continue
        variable = target_names[0]
        resource_id = call.args[0].value
        was: str | None = None
        was_node = next((item.value for item in call.keywords if item.arg == "was"), None)
        if was_node is not None:
            was = literal_string(was_node)
            if was is None or not was.strip():
                add_diagnostic(diagnostics, "E_RESOURCE_WAS", "error", f"was= on '{resource_id}' must be a non-empty string literal.", filename, node)
                was = None
            elif was == resource_id:
                add_diagnostic(diagnostics, "E_RESOURCE_WAS", "error", f"was= on '{resource_id}' equals the resource's own id.", filename, node)
                was = None
        if variable in resources or any(item["id"] == resource_id for item in resources.values()):
            add_diagnostic(diagnostics, "E_DUPLICATE_RESOURCE_ID", "error", f"Resource ID '{resource_id}' is declared more than once.", filename, node)
            continue
        kind = kinds[call.func.id]
        resources[variable] = {
            "id": resource_id,
            "kind": kind.value,
            "properties": properties,
            "stateful": kind in STATEFUL_KINDS,
        }
        if overrides:
            resources[variable]["overrides"] = overrides
        if was is not None:
            resources[variable]["was"] = was
        resource_nodes[variable] = node
    return resources, resource_nodes


def validate_resource_constructor_placement(
    tree: ast.Module,
    imports: set[str],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    valid_calls = {
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in imports
        and node.value.func.id in _RESOURCE_CONSTRUCTORS
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Constant)
        and isinstance(node.value.args[0].value, str)
        and (
            node.value.func.id != "Schedule"
            or any(item.arg == "cron" and literal_string(item.value) for item in node.value.keywords)
        )
    }
    pyifc_modules = plain_imported_modules(tree, only={"pyifc"})
    declaring_funcs = {id(call.func) for call in valid_calls}
    called_funcs = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in imports
            and node.id in _RESOURCE_CONSTRUCTORS
            and id(node) not in declaring_funcs
            and id(node) not in called_funcs
        ):
            add_diagnostic(
                diagnostics,
                "E_RESOURCE_DECLARATION",
                "error",
                f"'{node.id}' may only be called to declare a resource at module scope, not aliased or passed on.",
                filename,
                node,
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in pyifc_modules
            and node.func.attr in _RESOURCE_CONSTRUCTORS
        ):
            add_diagnostic(
                diagnostics,
                "E_RESOURCE_DECLARATION",
                "error",
                "Resources must be declared with a name imported by 'from pyifc import ...', at module scope.",
                filename,
                node,
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in imports
            and node.func.id in _RESOURCE_CONSTRUCTORS
            and node not in valid_calls
        ):
            add_diagnostic(
                diagnostics,
                "E_RESOURCE_DECLARATION",
                "error",
                "Resources must be declared at module scope with one literal logical name.",
                filename,
                node,
            )


def validate_unused_resources(
    resources: dict[str, dict[str, Any]],
    resource_nodes: dict[str, ast.AST],
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]],
    inferred: dict[tuple[str, str, Capability], tuple[str, int, int]],
    components: list[dict[str, Any]],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    """A resource no component declares, uses or is triggered by stays in the graph, with a warning."""
    referenced = {key[1] for key in declared.keys() | inferred.keys()}
    referenced.update(
        component["trigger"].split(":", 1)[1]
        for component in components
        if component["trigger"] and component["trigger"].startswith("schedule:")
    )
    for variable, resource in resources.items():
        if resource["id"] not in referenced:
            add_diagnostic(diagnostics, "W_UNUSED_RESOURCE", "warning", f"Resource '{resource['id']}' is declared but no component uses it.", filename, resource_nodes[variable])


def validate_name_collisions(
    resources: dict[str, dict[str, Any]],
    resource_nodes: dict[str, ast.AST],
    application_name: str,
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Two ids must not map to one Terraform address or one physical name.

    The generator derives both from the id, so a collision would silently
    overwrite one resource with the other. This uses the generator's own rules.
    """
    seen_address: dict[str, str] = {}
    seen_physical: dict[str, str] = {}
    for variable, resource in resources.items():
        resource_id = resource["id"]
        try:
            address = terraform_name(resource_id)
        except ValueError:
            continue
        physical = physical_suffix(resource_id, application_name)
        for seen, key, what in ((seen_address, address, "Terraform address"), (seen_physical, physical, "physical name")):
            other = seen.setdefault(key, resource_id)
            if other != resource_id:
                add_diagnostic(diagnostics, "E_NAME_COLLISION", "error", f"Resource ids '{other}' and '{resource_id}' map to the same {what} '{key}'.", filename, resource_nodes[variable])
                break


def validate_resource_rebinding(
    tree: ast.Module,
    resources: dict[str, dict[str, Any]],
    resource_nodes: dict[str, ast.AST],
    filename: str,
    diagnostics: list[Diagnostic],
) -> None:
    """A resource name must be bound exactly once, by its declaration.

    Rebinding it ('items = make()', 'global items', 'import x as items', a def or
    a parameter of that name) changes what the name refers to after the analysis
    has already attached capabilities to it.
    """
    defining = {id(node.targets[0]) for node in resource_nodes.values() if isinstance(node, ast.Assign)}
    resource_names = set(resources)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in resource_names and isinstance(node.ctx, (ast.Store, ast.Del)):
            if id(node) in defining:
                continue
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{node.id}' is rebound after its declaration.", filename, node)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in resource_names:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{name}' is declared global or nonlocal.", filename, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in resource_names:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{node.name}' is rebound by a definition.", filename, node)
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".", 1)[0]
            if bound in resource_names:
                add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{bound}' is rebound by an import.", filename, node)
        elif isinstance(node, ast.ExceptHandler) and node.name in resource_names:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{node.name}' is rebound by an except clause.", filename, node)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name in resource_names:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{node.name}' is rebound by a match pattern.", filename, node)
        elif isinstance(node, ast.MatchMapping) and node.rest in resource_names:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Resource name '{node.rest}' is rebound by a match pattern.", filename, node)
        elif isinstance(node, ast.arg) and node.arg in resource_names:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_REBINDING", "error", f"Parameter '{node.arg}' shadows a resource name.", filename, node)
