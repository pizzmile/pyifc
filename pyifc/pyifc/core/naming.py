"""Rules that turn a logical id into a Terraform address and a physical name suffix.

Shared by the analyzer (collision check) and the AWS backend (generation), so both apply the same rules.
"""

from __future__ import annotations

import re


def terraform_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_").lower()
    if not name:
        raise ValueError(f"Cannot create Terraform name from '{value}'.")
    if name[0].isdigit():
        name = f"resource_{name}"
    return name


def physical_suffix(resource_id: str, application_name: str) -> str:
    prefix = f"{application_name}-"
    suffix = resource_id.removeprefix(prefix)
    return suffix or resource_id
