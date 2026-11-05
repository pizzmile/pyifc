"""Deny-list of ways to reach a resource or an SDK by a name the analyzer cannot read."""

from __future__ import annotations

import ast

from pyifc.analysis.common import add_diagnostic
from pyifc.core.diagnostics import Diagnostic

# Ways to reach a name or module without writing it: the analyzer cannot follow them.
_DYNAMIC_NAMES = frozenset(
    {"eval", "exec", "compile", "vars", "globals", "locals", "__import__", "__builtins__", "getattr", "setattr", "delattr"}
)


_DYNAMIC_ATTRIBUTES = frozenset(
    {
        "__dict__", "__globals__", "__builtins__", "__closure__", "f_globals", "f_locals", "f_builtins", "f_back",
        "_getframe", "_current_frames", "currentframe", "import_module", "eval", "exec",
    }
)


_DYNAMIC_MODULES = frozenset({"importlib", "runpy", "pkgutil", "builtins", "inspect", "gc", "ctypes", "code", "codeop"})


_DYNAMIC_SYS_NAMES = frozenset({"modules", "_getframe", "_current_frames"})


def validate_dynamic_access(tree: ast.Module, filename: str, diagnostics: list[Diagnostic]) -> None:
    """Reject the common ways to reach a resource or an SDK by a name the analyzer cannot read.

    This is a deny-list: Python offers more routes than any list holds, and the
    ones not listed are accepted bypasses of the guarantee (see the evasion tests).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _DYNAMIC_NAMES:
            add_diagnostic(diagnostics, "E_UNSUPPORTED_DYNAMIC_ACCESS", "error", f"'{node.id}' reaches names the analyzer cannot follow.", filename, node)
        elif isinstance(node, ast.Attribute) and (
            node.attr in _DYNAMIC_ATTRIBUTES
            or (node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys")
        ):
            add_diagnostic(diagnostics, "E_UNSUPPORTED_DYNAMIC_ACCESS", "error", f"Attribute '{node.attr}' reaches names the analyzer cannot follow.", filename, node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _DYNAMIC_MODULES:
                    add_diagnostic(diagnostics, "E_UNSUPPORTED_DYNAMIC_ACCESS", "error", f"Importing '{alias.name}' loads code by a name the analyzer cannot follow.", filename, node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".", 1)[0] in _DYNAMIC_MODULES or (
                module == "sys" and any(alias.name in _DYNAMIC_SYS_NAMES for alias in node.names)
            ):
                add_diagnostic(diagnostics, "E_UNSUPPORTED_DYNAMIC_ACCESS", "error", f"Importing from '{module}' loads code by a name the analyzer cannot follow.", filename, node)
