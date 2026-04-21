"""
TurboPython import processing.

Constants and logic for handling import statements during parsing.
"""

from __future__ import annotations
import ast
from typing import Callable

from .nodes import (
    ParseError, SourceLocation, RelativeImportKey,
    TpyImport, ParseWarning,
)
from ..module_names import public_module_name

# Callback type for resolving star import exports.
# Takes a module name, returns the set of exported names, or None if not found.
StarImportResolver = Callable[[str], 'frozenset[str] | None']


# Implicit stdlib modules -- always compiled by the compiler, so imports from
# these modules don't need TpyImport nodes for __tpy_init() ordering.
_IMPLICIT_MODULES = frozenset({"typing", "tpy", "builtins"})

# Private submodule -> public module name overrides.
# Used when public_module_name() can't derive the correct public name
# (e.g. tpy._bootstrap._extern maps to tpy.extern, not tpy).
_PRIVATE_MODULE_PUBLIC_NAMES: dict[str, str] = {
    "tpy._bootstrap._extern": "tpy.extern",
    "tpy._typing": "typing",
}


class NonLiteralAllError(Exception):
    """Raised when __all__ is defined but not evaluable at compile time."""
    pass


def scan_star_exports(source: str) -> frozenset[str]:
    """Determine which names a module exports for 'from X import *'.

    If ``__all__`` is defined as a literal, returns exactly those names.
    If ``__all__`` is defined but not a compile-time literal, raises
    ``NonLiteralAllError``.
    Otherwise returns all public top-level names (functions, classes,
    assignments, annotated assignments, and imported names) whose name
    does not start with ``_`` -- matching CPython semantics.
    """
    tree = ast.parse(source)
    # Check for __all__ first (last assignment wins, matching CPython)
    all_value = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    all_value = node.value
        elif (isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name)
              and node.target.id == "__all__" and node.value):
            all_value = node.value
    if all_value is not None:
        try:
            return frozenset(ast.literal_eval(all_value))
        except (ValueError, TypeError):
            raise NonLiteralAllError(
                "__all__ is not a compile-time literal")

    # No __all__ -- collect all public top-level names (matching CPython)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom) and node.names:
            if node.names[0].name != "*":
                for alias in node.names:
                    local = alias.asname or alias.name
                    if not local.startswith("_"):
                        names.add(local)
    return frozenset(names)


def _read_module_all(module_path: str) -> frozenset[str]:
    """Read star exports from a stdlib .py module under lib/tpy/."""
    # Deferred: tpyc.__init__ imports tpyc.parse, so top-level would be circular.
    from tpyc import get_lib_dir
    path = get_lib_dir() / "tpy" / module_path
    if not path.exists():
        return frozenset()
    return scan_star_exports(path.read_text())


_module_all_cache: dict[str, frozenset[str]] = {}


def _get_module_all(module_path: str) -> frozenset[str]:
    """Get __all__ from a module file (cached)."""
    if module_path not in _module_all_cache:
        _module_all_cache[module_path] = _read_module_all(module_path)
    return _module_all_cache[module_path]


def get_tpy_exports() -> frozenset[str]:
    """Get the set of names exported by 'from tpy import *' (cached)."""
    return _get_module_all("tpy/__init__.py")


def get_builtins_exports() -> frozenset[str]:
    """Get the set of names exported by builtins (cached)."""
    return _get_module_all("builtins.py")


def get_typing_exports() -> frozenset[str]:
    """Get the set of names exported by typing (cached)."""
    return _get_module_all("typing.py")


def is_parser_keyword(module_name: str, name: str) -> bool:
    """Check if a specific name from a module is a parser keyword.

    Only one name remains hardcoded: tpy.extern.builtin_decorator, which
    must be recognized before any .py stubs can be compiled (bootstrap).
    """
    if "._" in module_name:
        pub = _PRIVATE_MODULE_PUBLIC_NAMES.get(module_name) or public_module_name(module_name)
        if pub == "tpy.extern":
            return name == "builtin_decorator"
    return False


class ImportProcessor:
    """Processes and tracks import statements during parsing.

    Separates import validation and tracking (semantic concerns)
    from pure syntax parsing.
    """

    def __init__(self, warn_fn, module_name: str | None = None,
                 is_package_init: bool = False,
                 star_import_resolver: StarImportResolver | None = None):
        self._warn = warn_fn
        self._module_name = module_name
        self._is_package_init = is_package_init
        self._star_import_resolver = star_import_resolver
        self.tpy_import_aliases: dict[str, str] = {}
        self.tpy_star_import: bool = False
        # Set of module names that had 'from X import *'
        self.star_imports: set[str] = set()
        # Reference to the module's imports dict, set during process_import_from.
        # Used by the parser for type resolution of imported builtin submodule types.
        self.imports: dict[str, set[tuple[str, str]] | None] | None = None
        # Reverse index: local_name -> (module_name, original_name) for O(1) lookup
        self._name_index: dict[str, tuple[str, str]] = {}
        # Local names whose import tuple has been rewritten by
        # `canonicalize_name_index` to point at the defining module
        # rather than a re-export facade.  `TypeResolver` consults this
        # set to gate `_module_qname` minting: parser-level aliases and
        # builtin-module lookups are NOT canonicalized and so stay as
        # bare `NominalType` without a qname.
        self._canonical_imports: set[str] = set()

    def _resolve_placeholder_module(self, key: str) -> str:
        """Resolve a relative import placeholder to a public module name.

        Uses the current module_name to compute the absolute path, then
        applies public_module_name to map private submodules to public parents.
        Falls back to the raw key if module_name is not set.

        For package __init__.py files, the module name IS the package, so
        level=1 means "within this package" (0 levels up from the package).
        For regular files, the module name includes the filename, so level=1
        means "same directory" (1 component stripped).
        """
        if not self._module_name:
            return key
        decoded = RelativeImportKey.decode(key)
        parts = self._module_name.split(".")
        # For __init__.py, package = module name; for regular files, package = parent
        package_parts = parts if self._is_package_init else parts[:-1]
        levels_up = decoded.level - 1
        if levels_up > len(package_parts):
            return key
        base_parts = package_parts[:len(package_parts) - levels_up]
        if decoded.partial:
            absolute = ".".join(base_parts + [decoded.partial])
        else:
            absolute = ".".join(base_parts)
        return _PRIVATE_MODULE_PUBLIC_NAMES.get(absolute) or public_module_name(absolute)

    def get_import_source(self, local_name: str) -> tuple[str, str] | None:
        """Find source module and original name for an imported name.

        Returns (module_name, original_name) or None.
        For relative imports from private submodules, the module name is
        resolved to the public parent (e.g. tpy._core._types -> tpy, tpy._builtins._list -> tpy).
        """
        return self._name_index.get(local_name)

    def canonicalize_name_index(
        self, lookup: Callable[[str, str], tuple[str, str] | None],
    ) -> None:
        """Rewrite name-index entries to point at defining modules.

        For each `(surface_module, original_name)` tuple, call `lookup`.
        When it returns a tuple, record the name as canonical
        (`is_canonical()` becomes True) and replace the entry if the
        lookup changed it.  Called by the compiler between parse and
        sema to redirect re-export facades to the module that actually
        declares the symbol, so `TypeResolver` can mint canonical
        `_module_qname` on its first pass -- but only for names whose
        lookup succeeded (records / enums / protocols), keeping aliases
        and other non-type imports free of qname pollution that would
        suppress downstream resolution passes.
        """
        for local_name, (surface, original) in list(self._name_index.items()):
            target = lookup(surface, original)
            if target is None:
                continue
            if target != (surface, original):
                self._name_index[local_name] = target
            self._canonical_imports.add(local_name)

    def is_canonical(self, local_name: str) -> bool:
        """True if `local_name`'s import tuple was canonicalized and
        points at the defining module for a record / enum / protocol."""
        return local_name in self._canonical_imports

    def _index_import(self, module_name: str, original: str, local: str) -> None:
        """Add a name to the reverse lookup index."""
        if RelativeImportKey.is_placeholder(module_name):
            resolved = self._resolve_placeholder_module(module_name)
            self._name_index[local] = (resolved, original)
        else:
            self._name_index[local] = (module_name, original)

    def _index_star_import(self, module_name: str, name: str) -> None:
        """Index a star-imported name, attributing stdlib re-exports correctly.

        If the name is a known tpy/builtins/typing export, bind it from the
        original stdlib module so the parser resolves types correctly (e.g.
        Int32 should always resolve as a tpy type, even when re-exported
        through a user module).
        """
        if name in get_tpy_exports():
            self._name_index[name] = ("tpy", name)
        elif name in get_builtins_exports():
            self._name_index[name] = ("builtins", name)
        elif name in get_typing_exports():
            self._name_index[name] = ("typing", name)
        else:
            self._index_import(module_name, name, name)

    def _resolve_star_import(self, module_name: str) -> frozenset[str] | None:
        """Resolve star import exports for a module.

        Tries the compiler-provided callback first, then falls back to
        hardcoded stdlib paths for standalone parser usage (tests, REPL).
        """
        if self._star_import_resolver:
            result = self._star_import_resolver(module_name)
            if result is not None:
                return result
        # Fallback for standalone parser (no compiler context)
        if module_name == "tpy":
            return get_tpy_exports()
        if module_name == "builtins":
            return get_builtins_exports()
        if module_name == "typing":
            return get_typing_exports()
        return None

    def _resolve_relative_to_absolute(self, level: int, partial: str | None) -> str | None:
        """Resolve a relative import to an absolute module name.

        Pure path computation without public_module_name mapping,
        so the result can be passed to the star import resolver.
        """
        if not self._module_name:
            return None
        parts = self._module_name.split(".")
        package_parts = parts if self._is_package_init else parts[:-1]
        levels_up = level - 1
        if levels_up > len(package_parts):
            return None
        base_parts = package_parts[:len(package_parts) - levels_up]
        if partial:
            return ".".join(base_parts + [partial])
        return ".".join(base_parts)

    def process_import(self, node: ast.Import, imports: dict, user_module_imports: dict,
                       top_level_stmts: list, module_aliases: dict,
                       bare_module_imports: set) -> None:
        """Process 'import X' or 'import X as Y' statement."""
        for alias in node.names:
            module_name = alias.name
            local_name = alias.asname or module_name
            user_module_imports[module_name] = node.lineno
            bare_module_imports.add(module_name)
            if module_name not in imports:
                imports[module_name] = set()
            if local_name != module_name:
                module_aliases[module_name] = local_name
            if module_name not in _IMPLICIT_MODULES:
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    import_alias = local_name if local_name != module_name else None
                    top_level_stmts.append(TpyImport(module_name=module_name, alias=import_alias, loc=SourceLocation(node.lineno, node.col_offset)))

    def process_import_from(self, node: ast.ImportFrom, imports: dict, user_module_imports: dict,
                            top_level_stmts: list, module_aliases: dict) -> None:
        """Process 'from X import Y' statement."""
        self.imports = imports
        module_name = node.module
        level = node.level

        # Handle relative imports (level > 0)
        if level > 0:
            key = RelativeImportKey(level=level, line=node.lineno, col=node.col_offset, partial=module_name or "")
            placeholder = key.encode()

            # Track as user module import
            user_module_imports[placeholder] = node.lineno

            imports[placeholder] = set()
            for alias in node.names:
                if alias.name == "*":
                    # Resolve relative -> absolute, then resolve exports
                    absolute_name = self._resolve_relative_to_absolute(level, module_name)
                    if absolute_name:
                        star_exports = self._resolve_star_import(absolute_name)
                        if star_exports is not None:
                            imports[placeholder] = {(name, name) for name in star_exports}
                            for name in star_exports:
                                if name not in self._name_index:
                                    self._index_star_import(placeholder, name)
                            self.star_imports.add(placeholder)
                            top_level_stmts.append(TpyImport(
                                module_name=placeholder, level=level,
                                relative_name=module_name,
                                loc=SourceLocation(node.lineno, node.col_offset)))
                            return
                    raise ParseError(
                        "'from ... import *': could not resolve module exports", node)
                local_name = alias.asname or alias.name
                imports[placeholder].add((alias.name, local_name))
                self._index_import(placeholder, alias.name, local_name)

            # Each relative import statement gets its own TpyImport
            top_level_stmts.append(TpyImport(
                module_name=placeholder,
                level=level,
                relative_name=module_name,
                loc=SourceLocation(node.lineno, node.col_offset)
            ))
            return

        if module_name is None:
            raise ParseError("Invalid import: no module name", node)

        # Skip __future__ imports -- CPython compatibility, no-op for TurboPython
        if module_name == "__future__":
            return

        # tpy has special star-import and alias tracking
        if module_name == "tpy":
            if any(alias.name == "*" for alias in node.names):
                exports = self._resolve_star_import("tpy")
                if not exports:
                    exports = frozenset()
                imports["tpy"] = {(name, name) for name in exports}
                for name in exports:
                    self._name_index[name] = ("tpy", name)
                self.tpy_star_import = True
                self.star_imports.add("tpy")
                return
            if "tpy" not in imports:
                imports["tpy"] = set()
            current = imports["tpy"]
            if current is not None:
                for alias in node.names:
                    original_name = alias.name
                    local_name = alias.asname if alias.asname else alias.name
                    current.add((original_name, local_name))
                    self._index_import("tpy", original_name, local_name)
                    self.tpy_import_aliases[local_name] = original_name
            return

        # Handle star imports for non-tpy modules
        if any(alias.name == "*" for alias in node.names):
            star_exports = self._resolve_star_import(module_name)
            if star_exports is None:
                raise ParseError(
                    f"'from {module_name} import *': could not resolve module exports", node)
            imports[module_name] = {(name, name) for name in star_exports}
            for name in star_exports:
                if name not in self._name_index:
                    self._index_star_import(module_name, name)
            self.star_imports.add(module_name)
            user_module_imports[module_name] = node.lineno
            if module_name not in _IMPLICIT_MODULES:
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    top_level_stmts.append(TpyImport(module_name=module_name, loc=SourceLocation(node.lineno, node.col_offset)))
            return

        # Track all imported names in the imports dict
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        has_non_keyword = False
        if current is not None:
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                current.add((alias.name, local_name))
                self._index_import(module_name, alias.name, local_name)
                if not is_parser_keyword(module_name, alias.name):
                    has_non_keyword = True

        # Trigger file resolution for any module with non-keyword names.
        if has_non_keyword or not any(is_parser_keyword(module_name, a.name) for a in node.names):
            user_module_imports[module_name] = node.lineno
            if module_name not in _IMPLICIT_MODULES:
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    top_level_stmts.append(TpyImport(module_name=module_name, loc=SourceLocation(node.lineno, node.col_offset)))
