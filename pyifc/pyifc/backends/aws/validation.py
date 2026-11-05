"""Checks that run before anything is emitted; every problem is collected, none is silently dropped."""

from __future__ import annotations

from typing import Any

from pyifc.backends.aws.mapping import (
    SCHEDULE_TRIGGER_PREFIX,
    SUPPORTED_CAPABILITIES,
    SUPPORTED_COMPONENT_KINDS,
    WORKER_TRIGGER_KINDS,
)
from pyifc.backends.aws.schedule import schedule_expression_problem


class UnsupportedByTarget(ValueError):
    """The specification uses something the AWS generator cannot emit.

    ``problems`` lists every offending item, not only the first.
    """

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__(
            "Specification cannot be generated for the AWS target:\n"
            + "\n".join(f"  - {problem}" for problem in self.problems)
        )


def unsupported_problems(specification: dict[str, Any]) -> list[str]:
    """Return every reason the specification is outside the supported matrix."""
    problems: list[str] = []
    resources = {resource["id"]: resource for resource in specification["resources"]}
    for resource in specification["resources"]:
        if resource["kind"] not in SUPPORTED_CAPABILITIES:
            problems.append(
                f"resource '{resource['id']}': kind '{resource['kind']}' is not supported "
                f"(supported kinds: {sorted(SUPPORTED_CAPABILITIES)})"
            )
        elif resource["kind"] == "schedule":
            problem = schedule_expression_problem(resource.get("properties", {}).get("cron"))
            if problem:
                problems.append(f"resource '{resource['id']}': schedule {problem}")
    for component in specification["components"]:
        kind = component["kind"]
        if kind not in SUPPORTED_COMPONENT_KINDS:
            problems.append(
                f"component '{component['id']}': kind '{kind}' is not supported "
                f"(supported kinds: {sorted(SUPPORTED_COMPONENT_KINDS)})"
            )
        elif kind == "worker":
            trigger = component.get("trigger") or ""
            prefix = next((item for item in WORKER_TRIGGER_KINDS if trigger.startswith(item)), None)
            if prefix is None:
                problems.append(
                    f"component '{component['id']}': worker trigger '{trigger}' is not supported "
                    "(only 'queue:<id>' and 'topic:<id>' triggers are)"
                )
            else:
                source_id = trigger.removeprefix(prefix)
                expected = WORKER_TRIGGER_KINDS[prefix]
                if source_id not in resources:
                    problems.append(
                        f"component '{component['id']}': worker trigger references unknown {expected} '{source_id}'"
                    )
                elif resources[source_id]["kind"] != expected:
                    problems.append(
                        f"component '{component['id']}': worker trigger '{trigger}' does not reference a {expected}"
                    )
        elif kind == "scheduled_job":
            trigger = component.get("trigger") or ""
            schedule_id = trigger.removeprefix(SCHEDULE_TRIGGER_PREFIX)
            if not trigger.startswith(SCHEDULE_TRIGGER_PREFIX):
                problems.append(
                    f"component '{component['id']}': scheduled_job trigger '{trigger}' is not supported "
                    "(only 'schedule:<id>' triggers are)"
                )
            elif schedule_id not in resources:
                problems.append(
                    f"component '{component['id']}': scheduled_job trigger references unknown schedule '{schedule_id}'"
                )
            elif resources[schedule_id]["kind"] != "schedule":
                problems.append(
                    f"component '{component['id']}': scheduled_job trigger '{trigger}' does not reference a schedule"
                )
    for capability in specification["capabilities"]:
        resource = resources.get(capability["resource_id"])
        if resource is None:
            problems.append(
                f"component '{capability['component_id']}': capability "
                f"'{capability['operation']}' references unknown resource '{capability['resource_id']}'"
            )
        elif resource["kind"] in SUPPORTED_CAPABILITIES and (
            capability["operation"] not in SUPPORTED_CAPABILITIES[resource["kind"]]
        ):
            problems.append(
                f"component '{capability['component_id']}': capability '{capability['operation']}' "
                f"on {resource['kind']} '{resource['id']}' is not supported "
                f"(supported: {sorted(SUPPORTED_CAPABILITIES[resource['kind']])})"
            )
    return problems


PROVENANCE_VALUES = ("declared", "used", "both", "implied")


def provenance_problems(specification: dict[str, Any]) -> list[str]:
    """Every capability edge must carry one of PROVENANCE_VALUES."""
    problems: list[str] = []
    for capability in specification["capabilities"]:
        value = capability.get("provenance")
        if value in PROVENANCE_VALUES:
            continue
        what = "has no provenance" if not value else f"has unknown provenance {value!r}"
        problems.append(
            f"component '{capability.get('component_id')}': capability '{capability.get('operation')}' "
            f"on '{capability.get('resource_id')}' {what} "
            "(a schema 1.3 specification carries provenance on every capability edge: "
            f"{list(PROVENANCE_VALUES)})"
        )
    return problems


def validate_specification(specification: dict[str, Any]) -> None:
    required = {"name", "resources", "components", "capabilities"}
    missing = required - specification.keys()
    if missing:
        raise ValueError(f"Specification is missing fields: {sorted(missing)}")
