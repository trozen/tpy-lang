"""
TurboPython Macro Module Loader

Discovers and loads ``# tpy: macro_module`` files via CPython during
compilation. Macro modules are NOT compiled to C++ -- they run at compile
time only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Callable


_MACRO_MODULE_RE = re.compile(r'^\s*#\s*tpy:\s+macro_module\s*$')


def is_macro_module_source(source: str) -> bool:
    """Quick check whether source text contains ``# tpy: macro_module``."""
    # Only scan the preamble (before first non-comment, non-blank line)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            break
        if _MACRO_MODULE_RE.match(line):
            return True
    return False


class MacroRegistry:
    """Registry of loaded class macros.

    Maps ``(module_name, decorator_name)`` to the Python callable that
    implements the macro.
    """

    def __init__(self) -> None:
        self._macros: dict[tuple[str, str], Callable] = {}
        self._loaded_modules: set[str] = set()

    def register(self, module: str, name: str, func: Callable) -> None:
        self._macros[(module, name)] = func

    def get_macro(self, module: str, name: str) -> Callable | None:
        return self._macros.get((module, name))

    def is_loaded(self, module_name: str) -> bool:
        return module_name in self._loaded_modules

    def load_module(self, module_name: str, file_path: Path) -> None:
        """Load a macro module via CPython and register its exports.

        Functions decorated with ``@class_macro`` are registered automatically.
        """
        if module_name in self._loaded_modules:
            return

        # Ensure tpyc is importable (macro modules import from tpyc.macro_api)
        tpyc_parent = str(Path(__file__).resolve().parent.parent)
        if tpyc_parent not in sys.path:
            sys.path.insert(0, tpyc_parent)

        spec = importlib.util.spec_from_file_location(
            f"_tpy_macro_{module_name}", str(file_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Cannot load macro module '{module_name}' from {file_path}"
            )

        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            raise RuntimeError(
                f"Error executing macro module '{module_name}' ({file_path}): {e}"
            ) from e

        # Scan for @class_macro decorated functions
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if callable(obj) and getattr(obj, '_is_class_macro', False):
                self.register(module_name, attr_name, obj)

        self._loaded_modules.add(module_name)
