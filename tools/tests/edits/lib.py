"""Exact string edits on a scratch copy of a sensor hub port.

Every replacement must match the expected number of times (default once). A miss stops the
run: a silent no-op would make a scenario look like T0.
"""
from pathlib import Path


class EditError(Exception):
    pass


def sub(root, rel, old, new, count=1):
    path = Path(root) / rel
    text = path.read_text()
    found = text.count(old)
    if found != count:
        raise EditError(f"{rel}: anchor found {found} times, expected {count}:\n{old}")
    path.write_text(text.replace(old, new))


def add_file(root, rel, text):
    path = Path(root) / rel
    if path.exists():
        raise EditError(f"{rel} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
