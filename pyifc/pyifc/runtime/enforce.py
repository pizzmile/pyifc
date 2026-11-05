"""Capability enforcement: a proxy that exposes only the methods a component may call."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyifc.core.models import METHOD_CAPABILITY


class CapabilityError(PermissionError):
    """A component called a resource method its declared capabilities do not cover."""


def restrict(
    adapter: object, kind: str, allowed: frozenset[str], *, component_id: str
) -> object:
    """Wrap ``adapter`` so only methods whose capability is in ``allowed`` can be called.

    A method of the kind whose capability is not allowed raises ``CapabilityError`` when
    called; every other name (including private ones and ``__dict__``) raises
    ``AttributeError``. The proxy holds no state of its own: the adapter is reachable only
    through closures, and the callables it returns are plain functions (no ``__self__``,
    no ``__wrapped__``).
    """
    kind = str(kind)
    permitted = frozenset(str(item) for item in allowed)
    methods = {name: capability for (k, name), capability in METHOD_CAPABILITY.items() if k == kind}

    class RestrictedAdapter:
        __slots__ = ()

        def __getattribute__(self, name: str) -> Callable[..., Any]:
            capability = methods.get(name)
            if capability is None:
                raise AttributeError(f"{kind} resource has no method '{name}'")
            if capability.value not in permitted:
                def denied(*args: Any, **kwargs: Any) -> Any:
                    raise CapabilityError(
                        f"component '{component_id}' called {kind}.{name}() but did not "
                        f"declare the '{capability.value}' capability"
                    )

                return denied
            target = getattr(adapter, name)  # AttributeError if the adapter lacks the method

            def forward(*args: Any, **kwargs: Any) -> Any:
                return target(*args, **kwargs)

            return forward

    return RestrictedAdapter()
