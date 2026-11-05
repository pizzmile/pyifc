"""The lock file ``pyifc.lock.json``: one entry per resource, read and written atomically.

Each entry records the kind, the physical name chosen at creation, the use signature and the
properties of a resource, so that a later generation can tell a rename from a removal.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

LOCK_FILE_NAME = "pyifc.lock.json"
LOCK_VERSION = 1


class LockError(ValueError):
    """The lock file exists but is not a valid version 1 lock."""


@dataclass(frozen=True)
class LockEntry:
    kind: str
    physical_name: str
    signature: tuple[str, ...]
    properties: dict


def load_lock(path: Path) -> dict[str, LockEntry] | None:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as error:
        raise LockError(f"{path}: lock file is not valid UTF-8: {error}") from error
    except OSError as error:
        raise LockError(f"{path}: cannot read lock file: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise LockError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise LockError(f"{path}: the top level must be an object")
    version = data.get("version")
    if isinstance(version, bool) or version != LOCK_VERSION:
        raise LockError(f"{path}: unsupported lock version {version!r}, expected {LOCK_VERSION}")
    resources = data.get("resources")
    if not isinstance(resources, dict):
        raise LockError(f"{path}: 'resources' must be an object")
    lock: dict[str, LockEntry] = {}
    for rid, raw in resources.items():
        if not isinstance(raw, dict):
            raise LockError(f"{path}: resource '{rid}' must be an object")
        for key in ("kind", "physical_name", "signature", "properties"):
            if key not in raw:
                raise LockError(f"{path}: resource '{rid}' is missing '{key}'")
        if not isinstance(raw["kind"], str) or not isinstance(raw["physical_name"], str):
            raise LockError(f"{path}: resource '{rid}': 'kind' and 'physical_name' must be strings")
        signature = raw["signature"]
        if not isinstance(signature, list) or not all(isinstance(item, str) for item in signature):
            raise LockError(f"{path}: resource '{rid}': 'signature' must be a list of strings")
        if not isinstance(raw["properties"], dict):
            raise LockError(f"{path}: resource '{rid}': 'properties' must be an object")
        lock[rid] = LockEntry(raw["kind"], raw["physical_name"], tuple(signature), raw["properties"])
    return lock


def write_lock(path: Path, lock: dict[str, LockEntry]) -> None:
    path = Path(path)
    data = {
        "version": LOCK_VERSION,
        "resources": {
            rid: {
                "kind": item.kind,
                "physical_name": item.physical_name,
                "signature": list(item.signature),
                "properties": item.properties,
            }
            for rid, item in lock.items()
        },
    }
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
