"""A finding of the analyzer or of the identity check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    file: str
    line: int
    column: int
