"""Inference of resource uses from the body of a component."""

from __future__ import annotations

import ast
from typing import Any

from pyifc.analysis.common import add_diagnostic, node_location
from pyifc.analysis.imports import is_foreign_resource_call
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    METHOD_CAPABILITY,
    Capability,
    ResourceKind,
)


def infer_function_uses(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    component_id: str,
    resources: dict[str, dict[str, Any]],
    resource_nodes: dict[str, ast.AST],
    inferred: dict[tuple[str, str, Capability], tuple[str, int, int]],
    filename: str,
    diagnostics: list[Diagnostic],
    foreign_names: frozenset[str] = frozenset(),
) -> None:
    resource_names = set(resources)
    aliases: set[str] = set()
    body_nodes = [child for statement in function.body for child in ast.walk(statement)]
    parents = {
        child: parent
        for parent in body_nodes
        for child in ast.iter_child_nodes(parent)
    }
    for node in body_nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested_resource_calls = [
                child
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in resource_names
            ]
            if nested_resource_calls:
                add_diagnostic(
                    diagnostics,
                    "E_UNSUPPORTED_NESTED_RESOURCE_USE",
                    "error",
                    "Resource operations inside nested functions are unsupported in v1.",
                    filename,
                    nested_resource_calls[0],
                )
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in resource_names:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_ALIAS", "error", f"Resource alias '{target.id}' is outside the supported analyzer subset.", filename, node)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in resource_names:
                add_diagnostic(diagnostics, "E_UNSUPPORTED_INDIRECT_USE", "error", "getattr-based resource access is unsupported.", filename, node)
            for argument in node.args:
                if isinstance(argument, ast.Name) and argument.id in resource_names:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_INDIRECT_USE", "error", f"Resource '{argument.id}' passed to another call is unsupported.", filename, node)
            for keyword in node.keywords:
                if isinstance(keyword.value, ast.Name) and keyword.value.id in resource_names:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_INDIRECT_USE", "error", f"Resource '{keyword.value.id}' passed to another call is unsupported.", filename, node)
            if is_foreign_resource_call(node, foreign_names):
                assert isinstance(node.func, ast.Attribute)
                add_diagnostic(diagnostics, "E_UNSUPPORTED_CROSS_MODULE_RESOURCE", "error", f"'{ast.unparse(node.func.value)}' comes from another module; resources must be declared in the analyzed module.", filename, node)
                continue
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                variable = node.func.value.id
                if variable in aliases:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_ALIAS", "error", f"Calls through resource alias '{variable}' are unsupported.", filename, node)
                    continue
                if variable not in resources:
                    continue
                resource = resources[variable]
                operation = METHOD_CAPABILITY.get((ResourceKind(resource["kind"]).value, node.func.attr))
                if operation is None:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_RESOURCE_METHOD", "error", f"Method '{node.func.attr}' is not supported for resource '{resource['id']}'.", filename, node)
                    continue
                key = (component_id, resource["id"], operation)
                inferred.setdefault(key, node_location(node))
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in resource_names:
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Call)
                and parent.func is node
            ):
                add_diagnostic(diagnostics, "E_UNSUPPORTED_INDIRECT_USE", "error", f"Resource method '{node.attr}' is used indirectly.", filename, node)
        elif isinstance(node, ast.Name) and node.id in resource_names and isinstance(node.ctx, ast.Load):
            parent = parents.get(node)
            if not isinstance(parent, ast.Attribute) or parent.value is not node:
                if not isinstance(parent, ast.Call):
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_INDIRECT_USE", "error", f"Resource '{node.id}' is used outside a direct method call.", filename, node)
