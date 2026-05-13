"""
TurboPython Module Resolver

Resolves module names to file paths, supporting both flat modules and packages.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path


# Path.exists() is case-insensitive on macOS APFS and Windows, so a lookup
# for `tplib/Box.py` succeeds when only `tplib/box.py` exists. We list the
# parent directory and check the requested name with case-sensitive `in`.
def _listdir(parent: Path) -> set[str]:
    try:
        return set(os.listdir(parent))
    except OSError:
        return set()


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
    the entry point file), then each extra directory in order.

    Supports:
    - Flat modules: "utils" -> utils.py
    - Dotted module paths: "mypackage.submod" -> mypackage/submod.py
    - Package imports: "mypackage" -> mypackage/__init__.py
    - Namespace packages: Submodule imports work without __init__ files
    """
    base_dir: Path
    extra_dirs: list[Path] = field(default_factory=list)

    def resolve(self, module_path: str) -> ResolvedModule | None:
        """Resolve a module path to a file.

        Searches base_dir first, then each extra_dir in order (first match wins).

        Args:
            module_path: The dotted module path to resolve (e.g., "mypackage.submod").

        Returns:
            ResolvedModule with path and metadata, or None if not found.
        """
        result = self._resolve_in(self.base_dir, module_path)
        if result:
            return result
        for d in self.extra_dirs:
            result = self._resolve_in(d, module_path)
            if result:
                return result
        return None

    def _resolve_in(self, base: Path, module_path: str) -> ResolvedModule | None:
        """Resolve a module path relative to a single directory.

        Args:
            base: Directory to search in.
            module_path: The dotted module path to resolve.

        Returns:
            ResolvedModule with path and metadata, or None if not found.
        """
        parts = module_path.split('.')

        # Navigate parent packages with case-sensitive checks, so that on
        # case-insensitive filesystems (macOS APFS, Windows) `from pkg import
        # Foo` does NOT match a submodule `pkg/foo.py`.
        current = base
        for part in parts[:-1]:
            if part not in _listdir(current) or not (current / part).is_dir():
                return None
            current = current / part

        final = parts[-1]
        package_name = '.'.join(parts[:-1]) if len(parts) > 1 else None
        entries = _listdir(current)

        py_name = f"{final}.py"
        if py_name in entries and (current / py_name).is_file():
            return ResolvedModule(
                path=current / py_name,
                canonical_name=module_path,
                package_name=package_name
            )

        if final in entries and (current / final).is_dir():
            pkg_dir = current / final
            if "__init__.py" in _listdir(pkg_dir) and (pkg_dir / "__init__.py").is_file():
                return ResolvedModule(
                    path=pkg_dir / "__init__.py",
                    canonical_name=module_path,
                    package_name=module_path,
                    is_package_init=True
                )

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
    def get_module_name(path: Path, extra_extensions: frozenset[str] = frozenset()) -> str:
        """Extract module name from path.

        Strips `.py` always, plus any extension listed in
        `extra_extensions` (used for frontend-plugin source files
        like `.pas`). Examples:
            utils.py -> utils
            hello.pas (with `.pas` in extra_extensions) -> hello
            __init__.py -> __init__
        """
        name = path.name
        if name.endswith(".py"):
            return name[:-3]
        for ext in extra_extensions:
            if name.endswith(ext):
                return name[: -len(ext)]
        return name
