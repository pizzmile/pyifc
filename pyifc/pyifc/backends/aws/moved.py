"""Terraform ``moved`` entries for renamed resources and components."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pyifc.core.naming import terraform_name


def build_moved_entries(
    terraform_resources: dict[str, Any], moved: Sequence[tuple[str, str]]
) -> list[dict[str, str]]:
    """One {"from", "to"} entry per emitted address that embeds a renamed Terraform name.

    The old address is the new one with the old Terraform name; an address that
    still exists under its old name is never a source (Terraform rejects that).
    """
    existing = {
        (block_type, name)
        for block_type, blocks in terraform_resources.items()
        for name in blocks
    }
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for old_id, new_id in moved:
        old_name, new_name = terraform_name(old_id), terraform_name(new_id)
        if old_name == new_name:
            continue
        for block_type, name in sorted(existing):
            source = f"{block_type}.{old_name}"
            if name != new_name or (block_type, old_name) in existing or source in seen:
                continue
            seen.add(source)
            entries.append({"from": source, "to": f"{block_type}.{new_name}"})
    return entries


def moved_problems(specification: dict[str, Any], moved: Sequence[tuple[str, str]]) -> list[str]:
    """Reasons the (old id, new id) pairs cannot be used for moved entries."""
    known = {item["id"] for item in specification["resources"]} | {
        item["id"] for item in specification["components"]
    }
    problems: list[str] = []
    for old_id, new_id in moved:
        if old_id == new_id:
            problems.append(f"moved pair ('{old_id}', '{new_id}') has identical ids")
        elif new_id not in known:
            problems.append(
                f"moved pair ('{old_id}', '{new_id}'): new id '{new_id}' is neither a resource nor a component"
            )
    return problems
