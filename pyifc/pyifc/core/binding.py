"""The adapters bound to the running component.

A resource object such as ``Bucket`` holds no connection. Its methods ask ``adapter()`` for the
implementation bound to its logical name, and the runner (local or Lambda) wraps each component
call in ``bind()``. This module sits in ``core`` because both the authoring objects and the
runtimes depend on it, and neither may depend on the other.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_adapters: ContextVar[dict[str, Any] | None] = ContextVar("pyifc_adapters", default=None)


@contextmanager
def bind(adapters: dict[str, Any]) -> Iterator[None]:
    token = _adapters.set(adapters)
    try:
        yield
    finally:
        _adapters.reset(token)


def adapter(logical_name: str) -> Any:
    try:
        return (_adapters.get() or {})[logical_name]
    except KeyError:
        raise RuntimeError(f"No runtime adapter bound for {logical_name}") from None
