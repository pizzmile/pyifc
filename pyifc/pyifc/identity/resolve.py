"""Resource identity: compare the current graph with the lock to find renames and removals.

Renaming a logical id keeps the physical resource instead of destroying and recreating it.
A rename is either declared (``was="old"``) or inferred when one resource of a kind disappears
and one of the same kind, properties and use signature appears.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pyifc.core.diagnostics import Diagnostic
from pyifc.core.models import STATEFUL_KINDS
from pyifc.identity.lock import LOCK_FILE_NAME, LockEntry


@dataclass(frozen=True)
class IdentityResult:
    physical_names: dict[str, str]
    moved: list[tuple[str, str]]
    diagnostics: tuple[Diagnostic, ...]
    lock: dict[str, LockEntry] = field(default_factory=dict)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "error")


def _signature(resource: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(resource.get("use_signature") or ()))


def _same_content(entry: LockEntry, resource: dict[str, Any]) -> bool:
    return entry.properties == resource.get("properties", {}) and tuple(sorted(entry.signature)) == _signature(resource)


def resolve_identity(
    spec: dict,
    lock: dict[str, LockEntry] | None,
    *,
    physical_name_for: Callable[[str], str],
    destroy: frozenset[str] = frozenset(),
    init: bool = False,
) -> IdentityResult:
    diagnostics: list[Diagnostic] = []

    def report(code: str, severity: str, message: str) -> None:
        diagnostics.append(Diagnostic(code, severity, message, LOCK_FILE_NAME, 1, 0))

    if lock is None and not init:
        report(
            "E_LOCK_MISSING",
            "error",
            f"{LOCK_FILE_NAME} does not exist. Create it with --init-lock on the first generation; "
            "without it renames and removals cannot be detected.",
        )
        return IdentityResult({}, [], tuple(diagnostics), {})

    old: dict[str, LockEntry] = dict(lock or {})
    current: dict[str, dict[str, Any]] = {item["id"]: item for item in spec.get("resources", [])}

    physical: dict[str, str] = {}
    moved: list[tuple[str, str]] = []
    consumed: set[str] = set()  # old ids that a rename took over
    handled: set[str] = set()  # current ids resolved by a rename or kind error

    # Same id, different kind.
    for rid, resource in current.items():
        if rid in old and old[rid].kind != resource["kind"]:
            report(
                "E_KIND_CHANGED",
                "error",
                f"Resource '{rid}' changed kind from '{old[rid].kind}' to '{resource['kind']}'. "
                "Use a new id for the new resource and remove the old one.",
            )
            handled.add(rid)
            consumed.add(rid)

    # Declared renames (was="old"). A was on an id that is already in the lock is a
    # leftover from an earlier rename and is ignored.
    claims: dict[str, list[str]] = {}
    for rid, resource in current.items():
        source = resource.get("was")
        if source and rid not in old:
            claims.setdefault(source, []).append(rid)
    for source, targets in sorted(claims.items()):
        if len(targets) > 1:
            report(
                "E_RENAME_AMBIGUOUS",
                "error",
                f"Resources {sorted(targets)} all declare was='{source}'. Only one resource can take over '{source}'.",
            )
            handled.update(targets)
            continue
        target = targets[0]
        handled.add(target)
        if source not in old:
            report(
                "E_RENAME_UNKNOWN_SOURCE",
                "error",
                f"Resource '{target}' declares was='{source}' but '{source}' is not in {LOCK_FILE_NAME}.",
            )
        elif source in current:
            report(
                "E_RENAME_UNKNOWN_SOURCE",
                "error",
                f"Resource '{target}' declares was='{source}' but '{source}' is still declared.",
            )
        elif old[source].kind != current[target]["kind"]:
            report(
                "E_KIND_CHANGED",
                "error",
                f"Resource '{target}' declares was='{source}' but its kind changed from "
                f"'{old[source].kind}' to '{current[target]['kind']}'.",
            )
        else:
            physical[target] = old[source].physical_name
            moved.append((source, target))
            consumed.add(source)
            report("W_RENAME_DECLARED", "warning", f"Resource '{source}' renamed to '{target}' (declared with was).")

    removed = [rid for rid in old if rid not in current and rid not in consumed]
    added = [rid for rid in current if rid not in old and rid not in handled]

    # An explicit --destroy takes the removed resource out of rename matching.
    destroyed = [rid for rid in removed if rid in destroy]
    removed = [rid for rid in removed if rid not in destroy]
    for rid in destroyed:
        report("W_RESOURCE_REMOVED", "warning", f"Resource '{rid}' is removed and will be destroyed (--destroy).")

    # Inferred renames, matched per kind.
    leftover_removed: list[str] = []
    for kind in sorted({old[rid].kind for rid in removed} | {current[rid]["kind"] for rid in added}):
        group_removed = [rid for rid in removed if old[rid].kind == kind]
        group_added = [rid for rid in added if current[rid]["kind"] == kind]
        if not group_added:
            leftover_removed.extend(group_removed)
            continue
        if not group_removed:
            continue
        if len(group_removed) == 1 and len(group_added) == 1 and _same_content(old[group_removed[0]], current[group_added[0]]):
            source, target = group_removed[0], group_added[0]
            physical[target] = old[source].physical_name
            moved.append((source, target))
            consumed.add(source)
            handled.add(target)
            report(
                "W_RENAME_INFERRED",
                "warning",
                f"Resource '{source}' looks renamed to '{target}' (same kind, properties and usage). "
                f"Add was='{source}' to confirm it.",
            )
            continue
        report(
            "E_RENAME_AMBIGUOUS",
            "error",
            f"Cannot tell whether {kind} resources were renamed: removed {sorted(group_removed)}, "
            f"added {sorted(group_added)}. Declare the old id with was=... on the new resource, "
            "pass --destroy <id> for each removed id to destroy it, or keep the id unchanged.",
        )
        handled.update(group_added)
        consumed.update(group_removed)

    for rid in sorted(leftover_removed):
        if old[rid].kind in STATEFUL_KINDS:
            report(
                "E_STATEFUL_REMOVED",
                "error",
                f"Stateful resource '{rid}' ({old[rid].kind}) is no longer declared. "
                f"Pass --destroy {rid} to destroy it, or declare it again.",
            )
        else:
            report("W_RESOURCE_REMOVED", "warning", f"Resource '{rid}' ({old[rid].kind}) is no longer declared and will be removed.")

    for rid in current:
        if rid in physical or rid in handled:
            continue
        physical[rid] = old[rid].physical_name if rid in old else physical_name_for(rid)

    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        new_lock = old
    else:
        new_lock = {
            rid: LockEntry(resource["kind"], physical[rid], _signature(resource), resource.get("properties", {}))
            for rid, resource in sorted(current.items())
        }
    return IdentityResult(physical, moved, tuple(diagnostics), new_lock)
