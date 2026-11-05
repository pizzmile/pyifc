"""Running components: load the application, enforce capabilities, adapt to local files or AWS.

Depends on ``core`` and ``authoring``.
"""

from pyifc.runtime.loader import find_registry, load_app_module, resolve_function

__all__ = ["find_registry", "load_app_module", "resolve_function"]
