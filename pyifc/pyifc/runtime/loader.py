"""Loading an application module and resolving the functions its components point to."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import importlib
import importlib.util
import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from pyifc.authoring.registry import ApplicationRegistry


def load_app_module(reference: str) -> ModuleType:
    """Import the application module named by ``reference``.

    A reference that ends in ``.py`` or contains a path separator is a file, executed
    fresh under a unique module name; anything else is a dotted module name.
    """
    if not (reference.endswith(".py") or os.sep in reference or "/" in reference):
        return importlib.import_module(reference)
    path = Path(reference).resolve()
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
    name = f"pyifc_app_{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load application module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def find_registry(module: ModuleType) -> ApplicationRegistry:
    """Return the registry of the ``Application`` the module declares."""
    from pyifc.authoring.application import Application

    for value in vars(module).values():
        if isinstance(value, Application):
            return value.registry
        if isinstance(value, ApplicationRegistry):
            return value
    raise RuntimeError(f"Module '{module.__name__}' declares no pyifc Application")


def resolve_function(module: ModuleType, callable_ref: str) -> Callable[..., Any]:
    """Return the plain function behind a ``module:qualname`` reference.

    Target wrappers (``PYIFC_TARGET=aws``) are unwrapped through ``__wrapped__``, and
    coroutine functions are run to completion so the synchronous adapters can call them.
    """
    module_name, _, qualname = callable_ref.partition(":")
    target: Any = module if module_name == module.__name__ else importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    return _as_sync(inspect.unwrap(target))


def _as_sync(function: Callable[..., Any]) -> Callable[..., Any]:
    if not inspect.iscoroutinefunction(function):
        return function

    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return run
