"""The outcome of an analysis: a specification, or diagnostics that explain why there is none."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from pyifc.core.diagnostics import Diagnostic


@dataclass(frozen=True)
class AnalysisResult:
    specification: dict[str, Any] | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "specification": self.specification,
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
