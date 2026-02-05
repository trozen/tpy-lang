"""
TurboPython Module Resolver

Resolves module names to file paths, supporting both flat modules and packages.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResolvedModule:
    """Result of module resolution.

    Attributes:
        path: Path to the module file.
        canonical_name: Dotted module name (e.g., "mypackage.submod").
        package_name: Parent package name or None for top-level modules.
        is_package_init: True if this is a package __init__ file.
    """
    path: Path
    canonical_name: str
    package_name: str | None = None
    is_package_init: bool = False


@dataclass
class ModuleResolver:
    """Resolves module names to file paths.

    Modules are resolved relative to a base directory (the directory containing
    the entry point file). Resolution order: .tp.py first, then .py.

    Supports:
    - Flat modules: "utils" -> utils.tp.py or utils.py
    - Dotted module paths: "mypackage.submod" -> mypackage/submod.tp.py
    - Package imports: "mypackage" -> mypackage/__init__.tp.py
    - Namespace packages: Submodule imports work without __init__ files
    """
    base_dir: Path

    def resolve(self, module_path: str) -> ResolvedModule | None:
        """Resolve a module path to a file.

        Supports dotted module paths (e.g., "mypackage.submod") and packages.
        Uses namespace package semantics (no __init__ required for submodule imports).

        Args:
            module_path: The dotted module path to resolve (e.g., "mypackage.submod").

        Returns:
            ResolvedModule with path and metadata, or None if not found.
        """
        parts = module_path.split('.')

        # Navigate directories for parent packages (namespace package semantics)
        current = self.base_dir
        for part in parts[:-1]:
            current = current / part
            if not current.is_dir():
                return None

        # Final component: try as module file first, then as package directory
        final = parts[-1]
        package_name = '.'.join(parts[:-1]) if len(parts) > 1 else None

        # Try module file: .tp.py then .py
        for ext in [".tp.py", ".py"]:
            candidate = current / f"{final}{ext}"
            if candidate.exists():
                return ResolvedModule(
                    path=candidate,
                    canonical_name=module_path,
                    package_name=package_name
                )

        # Try as package with __init__: dir/__init__.tp.py
        pkg_dir = current / final
        if pkg_dir.is_dir():
            init = self._find_init(pkg_dir)
            if init:
                return ResolvedModule(
                    path=init,
                    canonical_name=module_path,
                    package_name=module_path,
                    is_package_init=True
                )

        return None

    def _find_init(self, directory: Path) -> Path | None:
        """Find __init__ file in a directory.

        Args:
            directory: Directory to search.

        Returns:
            Path to __init__.tp.py or __init__.py, or None if not found.
        """
        for name in ["__init__.tp.py", "__init__.py"]:
            init = directory / name
            if init.exists():
                return init
        return None

    def resolve_relative(self, current_module: str, level: int,
                         partial_name: str | None,
                         is_package_init: bool = False) -> str | None:
        """Resolve relative import to absolute module path.

        Args:
            current_module: Importing module's canonical name (e.g., "pkg.submod").
            level: Number of dots (1=".", 2="..", etc.).
            partial_name: Module path after dots (None for "from . import X").
            is_package_init: True if current_module is a package __init__.

        Returns:
            Absolute module path, or None if would go above root.

        Examples:
            ("pkg.mod", 1, "sibling", False) -> "pkg.sibling"
            ("pkg.mod", 2, "other", False)   -> "other"
            ("pkg", 1, "utils", True)        -> "pkg.utils"  # __init__.py
            ("pkg", 1, None, True)           -> "pkg"        # from . import X
        """
        parts = current_module.split('.')

        # For __init__.py, package = module; for regular, package = parent
        package_parts = parts if is_package_init else parts[:-1]

        # level=1 is current package, level=2 is parent, etc.
        levels_up = level - 1

        if levels_up > len(package_parts):
            return None  # Beyond root

        base = package_parts[:len(package_parts) - levels_up]

        if partial_name:
            return '.'.join(base + partial_name.split('.'))
        # Return "" for top-level (base is empty) - this is valid for "from .. import X"
        # where X will be looked up as a top-level module
        return '.'.join(base)

    @staticmethod
    def get_module_name(path: Path) -> str:
        """Extract module name from path.

        Examples:
            utils.tp.py -> utils
            utils.py -> utils
            __init__.tp.py -> __init__

        Note: For canonical names of packages, use resolve() which returns
        the full dotted path.
        """
        name = path.name
        if name.endswith(".tp.py"):
            return name[:-6]
        elif name.endswith(".py"):
            return name[:-3]
        return name
