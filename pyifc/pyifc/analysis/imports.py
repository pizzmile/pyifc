"""Checks on what the analyzed module imports, and which names come from another module."""

from __future__ import annotations

import ast
import sys

from pyifc.analysis.common import add_diagnostic
from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import (
    METHOD_CAPABILITY,
)

_RESOURCE_METHOD_NAMES = frozenset(name for _, name in METHOD_CAPABILITY)


_SDK_MODULES = frozenset({"boto3", "botocore", "aioboto3", "aiobotocore"})


def imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pyifc":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def validate_pyifc_imports(tree: ast.Module, filename: str, diagnostics: list[Diagnostic]) -> None:
    """pyifc is imported only as 'from pyifc import Name', so no constructor is reachable another way."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pyifc" or alias.name.startswith("pyifc."):
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_IMPORT", "error", "Import pyifc names with 'from pyifc import Name'.", filename, node)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith("pyifc."):
            add_diagnostic(diagnostics, "E_UNSUPPORTED_IMPORT", "error", "Import pyifc names with 'from pyifc import Name'.", filename, node)


def validate_sdk_imports(tree: ast.Module, filename: str, diagnostics: list[Diagnostic]) -> None:
    """A direct cloud SDK call carries no capability, so the analyzer cannot check it."""
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module.split(".", 1)[0] in _SDK_MODULES:
                add_diagnostic(
                    diagnostics,
                    "E_DIRECT_SDK_USE",
                    "error",
                    f"Importing '{module}' is unsupported: SDK calls carry no capability and cannot be checked against a declaration.",
                    filename,
                    node,
                )


def foreign_imported_names(tree: ast.Module) -> frozenset[str]:
    """Names bound by 'from m import x' (m is not pyifc) at module level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and not (node.level == 0 and node.module == "pyifc"):
            names.update((alias.asname or alias.name) for alias in node.names)
    names.update(plain_imported_modules(tree, exclude=set(sys.stdlib_module_names) | {"pyifc"}))
    return frozenset(names)


def plain_imported_modules(
    tree: ast.Module,
    *,
    only: set[str] | None = None,
    exclude: set[str] | None = None,
) -> set[str]:
    """Local names bound by 'import m' or 'import m as n' (top-level package decides inclusion)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            package = alias.name.split(".", 1)[0]
            if only is not None and package not in only:
                continue
            if exclude is not None and package in exclude:
                continue
            names.add(alias.asname or package)
    return names


def is_foreign_resource_call(node: ast.AST, foreign_names: frozenset[str]) -> bool:
    """A resource-looking method call on an object that came from another module.

    Covers 'from m import x' then 'x.put(...)', and 'import m' then 'm.x.put(...)'.
    Standard-library modules are not foreign here ('os.environ.get' is harmless).
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in _RESOURCE_METHOD_NAMES:
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Name):
        return receiver.id in foreign_names
    return (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id in foreign_names
    )
