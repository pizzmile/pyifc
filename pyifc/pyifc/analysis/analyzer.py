"""Static analysis of an application module: entry points and assembly of the specification.

The module is parsed with ``ast`` and never imported or executed. The checks live in the
sibling modules; this one runs them in order and builds the specification.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pyifc.analysis.capabilities import (
    trigger_pairs,
    validate_capabilities,
    validate_queues,
)
from pyifc.analysis.common import add_diagnostic, location_dict
from pyifc.analysis.components import (
    find_application,
    parse_component,
    validate_component_references,
    validate_component_signature,
    validate_components,
    validate_helper_resource_uses,
)
from pyifc.analysis.dynamic import validate_dynamic_access
from pyifc.analysis.imports import (
    foreign_imported_names,
    imported_names,
    validate_pyifc_imports,
    validate_sdk_imports,
)
from pyifc.analysis.resources import (
    find_resources,
    validate_name_collisions,
    validate_resource_constructor_placement,
    validate_resource_rebinding,
    validate_unused_resources,
)
from pyifc.analysis.result import AnalysisResult
from pyifc.analysis.uses import infer_function_uses
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    Capability,
    ComponentKind,
)


def analyze_file(
    path: str | Path,
    *,
    source_root: str | Path | None = None,
) -> AnalysisResult:
    source_path = Path(path).resolve()
    root = Path(source_root).resolve() if source_root is not None else source_path.parent
    try:
        relative_path = source_path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Source file '{source_path}' is outside source root '{root}'.") from error
    module_parts = list(relative_path.with_suffix("").parts)
    if module_parts and module_parts[-1] == "__init__":
        module_parts.pop()
    return analyze_source(
        source_path.read_text(encoding="utf-8"),
        filename=relative_path.as_posix(),
        module_name=".".join(module_parts) or source_path.stem,
    )


def analyze_source(
    source: str,
    *,
    filename: str = "<memory>",
    module_name: str | None = None,
) -> AnalysisResult:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        diagnostic = Diagnostic(
            code="E_SYNTAX",
            severity="error",
            message=error.msg,
            file=filename,
            line=error.lineno or 1,
            column=max((error.offset or 1) - 1, 0),
        )
        return AnalysisResult(None, (diagnostic,))

    diagnostics: list[Diagnostic] = []
    imports = imported_names(tree)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pyifc":
            for alias in node.names:
                if alias.asname:
                    add_diagnostic(
                        diagnostics,
                        "E_UNSUPPORTED_IMPORT",
                        "error",
                        "Aliased pyifc imports are outside the supported analyzer subset.",
                        filename,
                        node,
                    )
    validate_sdk_imports(tree, filename, diagnostics)
    foreign_names = foreign_imported_names(tree)
    app_name, application_name = find_application(tree, imports)
    if app_name is None:
        add_diagnostic(
            diagnostics,
            "E_APPLICATION_DECLARATION",
            "error",
            "Source must declare an Application with a literal name.",
            filename,
            tree.body[0] if tree.body else ast.Pass(),
        )
    resources, resource_nodes = find_resources(tree, imports, filename, diagnostics)
    validate_resource_constructor_placement(tree, imports, filename, diagnostics)
    components: list[dict[str, Any]] = []
    component_nodes: set[ast.FunctionDef | ast.AsyncFunctionDef] = set()
    declared: dict[tuple[str, str, Capability], tuple[str, int, int]] = {}
    inferred: dict[tuple[str, str, Capability], tuple[str, int, int]] = {}
    component_ids: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    routes: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        component = parse_component(
            node,
            app_name,
            application_name,
            module_name or Path(filename).stem,
            resources,
            filename,
            declared,
            diagnostics,
        )
        if component is None:
            continue
        component_nodes.add(node)
        component_id = component["id"]
        if component_id in component_ids:
            add_diagnostic(
                diagnostics,
                "E_DUPLICATE_COMPONENT_ID",
                "error",
                f"Component ID '{component_id}' is declared by multiple functions.",
                filename,
                node,
            )
        else:
            component_ids[component_id] = node

        if component["kind"] == ComponentKind.HTTP_HANDLER.value:
            route = (component["properties"]["method"], component["properties"]["path"])
            if route in routes:
                add_diagnostic(
                    diagnostics,
                    "E_DUPLICATE_ROUTE",
                    "error",
                    f"HTTP route {route[0]} {route[1]} is declared more than once.",
                    filename,
                    node,
                )
            routes[route] = node

        components.append(component)
        infer_function_uses(
            node,
            component_id,
            resources,
            resource_nodes,
            inferred,
            filename,
            diagnostics,
            foreign_names,
        )

    validate_helper_resource_uses(tree, component_nodes, resources, filename, diagnostics, foreign_names)
    for node in component_nodes:
        validate_component_signature(node, app_name, resources, filename, diagnostics)
    validate_component_references(tree, component_nodes, filename, diagnostics)
    validate_pyifc_imports(tree, filename, diagnostics)
    validate_resource_rebinding(tree, resources, resource_nodes, filename, diagnostics)
    validate_dynamic_access(tree, filename, diagnostics)

    validate_capabilities(resources, declared, inferred, components, diagnostics, filename)
    validate_queues(resources, declared, filename, diagnostics)
    validate_unused_resources(resources, resource_nodes, declared, inferred, components, filename, diagnostics)
    validate_name_collisions(resources, resource_nodes, application_name, filename, diagnostics)
    validate_components(components, declared, filename, diagnostics)

    diagnostics.sort(key=lambda item: (item.file, item.line, item.column, item.code, item.message))
    if any(item.severity == "error" for item in diagnostics):
        return AnalysisResult(None, tuple(diagnostics))

    trigger_subscriptions = trigger_pairs(components)
    capabilities: list[dict[str, Any]] = []
    for key in sorted(declared.keys() | inferred.keys(), key=lambda item: (item[0], item[1], item[2].value)):
        component_id, resource_id, operation = key
        declaration_location = declared.get(key)
        inference_location = inferred.get(key)
        # A worker's subscribe on its own trigger has no call in its body: the trigger is its use.
        is_trigger = operation == Capability.SUBSCRIBE and (component_id, resource_id) in trigger_subscriptions
        provenance = "both" if declaration_location and (inference_location or is_trigger) else (
            "declared" if declaration_location else "used"
        )
        location = declaration_location or inference_location
        capabilities.append(
            {
                "component_id": component_id,
                "resource_id": resource_id,
                "operation": operation.value,
                "provenance": provenance,
                "source": location_dict(filename, location),
            }
        )

    signatures: dict[str, list[str]] = {resource["id"]: [] for resource in resources.values()}
    for item in capabilities:
        if item["provenance"] in ("both", "used"):
            signatures[item["resource_id"]].append(f"{item['component_id']}>{item['operation']}")
    for resource in resources.values():
        resource["use_signature"] = sorted(signatures[resource["id"]])

    specification = {
        "name": application_name,
        "schema_version": "1.3",
        "resources": sorted(resources.values(), key=lambda item: item["id"]),
        "components": sorted(components, key=lambda item: item["id"]),
        "capabilities": capabilities,
    }
    return AnalysisResult(specification, tuple(diagnostics))
