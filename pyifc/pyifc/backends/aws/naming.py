"""Physical names of resources: the default, the recorded one, and collisions."""

from __future__ import annotations

from typing import Any

from pyifc.core.naming import physical_suffix


def default_physical_name(resource_id: str, application_name: str) -> str:
    """The physical name a resource gets when nothing else is recorded for it."""
    return f"${{var.name_prefix}}-{physical_suffix(resource_id, application_name)}"


def physical_name(
    resource_id: str, application_name: str, physical_names: dict[str, str] | None = None
) -> str:
    if physical_names and resource_id in physical_names:
        return physical_names[resource_id]
    return default_physical_name(resource_id, application_name)


def physical_name_problems(
    specification: dict[str, Any], physical_names: dict[str, str] | None
) -> list[str]:
    """Reasons the resolved physical names are unusable: unknown ids, empty names, collisions."""
    problems: list[str] = []
    ids = [resource["id"] for resource in specification["resources"]]
    for resource_id, name in sorted((physical_names or {}).items()):
        if resource_id not in ids:
            problems.append(f"physical name for unknown resource '{resource_id}'")
        elif not isinstance(name, str) or not name:
            problems.append(f"resource '{resource_id}': physical name must be a non-empty string")
    owners: dict[str, list[str]] = {}
    for resource_id in ids:
        owners.setdefault(
            physical_name(resource_id, specification["name"], physical_names), []
        ).append(resource_id)
    for name, resource_ids in sorted(owners.items()):
        if len(resource_ids) > 1:
            problems.append(
                f"resources {sorted(resource_ids)} resolve to the same physical name '{name}'"
            )
    return problems
