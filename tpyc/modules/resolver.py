"""
TurboPython Module Resolver

Resolves module names to file paths.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ModuleResolver:
    """Resolves module names to file paths.

    Modules are resolved relative to a base directory (the directory containing
    the entry point file). Resolution order: .tp.py first, then .py.
    """
    base_dir: Path

    def resolve(self, module_name: str) -> Path | None:
        """Find module file: try .tp.py first, then .py.

        Args:
            module_name: The module name to resolve (e.g., "utils").

        Returns:
            Path to the module file if found, None otherwise.
        """
        # Prefer .tp.py (TurboPython-specific)
        tp_path = self.base_dir / f"{module_name}.tp.py"
        if tp_path.exists():
            return tp_path
        # Fallback to .py (standard Python, must be TPy-compatible)
        py_path = self.base_dir / f"{module_name}.py"
        if py_path.exists():
            return py_path
        return None

    @staticmethod
    def get_module_name(path: Path) -> str:
        """Extract module name from path.

        Examples:
            utils.tp.py -> utils
            utils.py -> utils
        """
        name = path.name
        if name.endswith(".tp.py"):
            return name[:-6]
        elif name.endswith(".py"):
            return name[:-3]
        return name
