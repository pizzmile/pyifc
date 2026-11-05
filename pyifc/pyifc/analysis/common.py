"""Helpers shared by the analysis modules: literals, source locations and diagnostics."""

from __future__ import annotations

import ast
from typing import Any

from pyifc.core.diagnostics import Diagnostic


def literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def node_location(node: ast.AST) -> tuple[str, int, int]:
    return ("", getattr(node, "lineno", 1), getattr(node, "col_offset", 0))


def location_dict(filename: str, location: tuple[str, int, int] | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {"file": filename, "line": location[1], "column": location[2]}


def add_diagnostic(
    diagnostics: list[Diagnostic],
    code: str,
    severity: str,
    message: str,
    filename: str,
    node: ast.AST,
) -> None:
    diagnostics.append(Diagnostic(code, severity, message, filename, getattr(node, "lineno", 1), getattr(node, "col_offset", 0)))


def add_diagnostic_at(
    diagnostics: list[Diagnostic],
    code: str,
    severity: str,
    message: str,
    filename: str,
    location: tuple[str, int, int] | None,
) -> None:
    line = location[1] if location else 1
    column = location[2] if location else 0
    diagnostics.append(Diagnostic(code, severity, message, filename, line, column))
