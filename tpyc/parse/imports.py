"""
TurboPython import processing.

Constants and logic for handling import statements during parsing.
"""

from __future__ import annotations
import ast

from .nodes import (
    ParseError, SourceLocation, RelativeImportKey,
    TpyImport, ParseWarning,
)
from ..typesys import public_module_name


# Names that the parser must resolve during parsing (compiler intrinsics).
# Most tpy names are now defined in .py stubs and resolved via normal imports.
# Only names that trigger AST cloning (auto_readonly, auto_own) remain here;
# other decorators are resolved via @builtin_decorator stubs in _decorators.py.
PARSER_KEYWORDS: dict[str, frozenset[str] | None] = {
    # None means ALL names are parser keywords (module cannot be shadowed by .py)
    "tpy": frozenset({
        # Decorators that trigger method cloning at parse time
        "auto_readonly", "auto_own",
    }),
    # tpy.extern is NOT listed here to avoid changing import processing for user code.
    # Extern keyword names are checked via _EXTERN_KEYWORDS in is_parser_keyword().
    "builtins": None,
    "__future__": None,  # no-op, never resolved as .py
    "typing": frozenset({"Optional", "Final", "Callable"}),
}

# Bootstrap primitive for tpy.extern: the one keyword that must be hardcoded
# so that _extern.py can define all other decorators using @builtin_decorator.
# All other extern decorator names are defined as @builtin_decorator stubs.
_EXTERN_KEYWORDS = frozenset({
    "builtin_decorator",
})

# Private submodule -> public module name overrides.
# Used when public_module_name() can't derive the correct public name
# (e.g. tpy._bootstrap._extern maps to tpy.extern, not tpy).
_PRIVATE_MODULE_PUBLIC_NAMES: dict[str, str] = {
    "tpy._bootstrap._extern": "tpy.extern",
    "tpy._typing": "typing",
}

# Python builtins -- always available without import
PYTHON_BUILTINS = frozenset({"int", "float", "bool", "str", "None", "tuple", "slice", "type", "Exception", "BaseException"})

# Names from typing that require explicit import
TYPING_NAMES = frozenset({"Optional", "Protocol", "Self", "Sized", "Sequence", "MutableSequence", "Iterator", "Iterable", "Final", "override", "overload", "Callable"})


def _read_tpy_exports() -> frozenset[str]:
    """Read __all__ from tpy/__init__.py to get star-import exports."""
    # Deferred: tpyc.__init__ imports tpyc.parse, so top-level would be circular.
    from tpyc import get_lib_dir
    init_path = get_lib_dir() / "tpy" / "tpy" / "__init__.py"
    if not init_path.exists():
        return frozenset()
    tree = ast.parse(init_path.read_text())
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return frozenset(ast.literal_eval(node.value))
    return frozenset()


_tpy_exports: frozenset[str] | None = None


def get_tpy_exports() -> frozenset[str]:
    """Get the set of names exported by 'from tpy import *' (cached)."""
    global _tpy_exports
    if _tpy_exports is None:
        _tpy_exports = _read_tpy_exports()
    return _tpy_exports


def is_parser_keyword_module(module_name: str) -> bool:
    """Check if a module has any parser keyword handling."""
    return module_name in PARSER_KEYWORDS


def is_parser_keyword(module_name: str, name: str) -> bool:
    """Check if a specific name from a module is a parser keyword.

    Returns True if the name must be handled by the parser (not from .py files).
    For modules with PARSER_KEYWORDS[mod] = None, ALL names are keywords.
    For private submodules (e.g. tpy._bootstrap._decorators), checks the public parent.
    """
    kw = PARSER_KEYWORDS.get(module_name)
    if kw is None and module_name in PARSER_KEYWORDS:
        return True  # None means all names are keywords
    if kw is not None:
        return name in kw
    # For private submodules, check the public module name
    if "._" in module_name:
        pub = _PRIVATE_MODULE_PUBLIC_NAMES.get(module_name) or public_module_name(module_name)
        if pub == "tpy.extern":
            return name in _EXTERN_KEYWORDS
        if pub != module_name:
            return is_parser_keyword(pub, name)
    return False


class ImportProcessor:
    """Processes and tracks import statements during parsing.

    Separates import validation and tracking (semantic concerns)
    from pure syntax parsing.
    """

    def __init__(self, warn_fn, module_name: str | None = None,
                 is_package_init: bool = False):
        self._warn = warn_fn
        self._module_name = module_name
        self._is_package_init = is_package_init
        self.tpy_import_aliases: dict[str, str] = {}
        self.tpy_star_import: bool = False
        # Reference to the module's imports dict, set during process_import_from.
        # Used by the parser for type resolution of imported builtin submodule types.
        self.imports: dict[str, set[tuple[str, str]] | None] | None = None
        # Reverse index: local_name -> (module_name, original_name) for O(1) lookup
        self._name_index: dict[str, tuple[str, str]] = {}

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

    def _index_import(self, module_name: str, original: str, local: str) -> None:
        """Add a name to the reverse lookup index."""
        if RelativeImportKey.is_placeholder(module_name):
            resolved = self._resolve_placeholder_module(module_name)
            self._name_index[local] = (resolved, original)
        else:
            self._name_index[local] = (module_name, original)

    def process_import(self, node: ast.Import, imports: dict, user_module_imports: dict,
                       top_level_stmts: list, module_aliases: dict,
                       bare_module_imports: set) -> None:
        """Process 'import X' or 'import X as Y' statement."""
        for alias in node.names:
            module_name = alias.name
            local_name = alias.asname or module_name
            has_keywords = is_parser_keyword_module(module_name)
            if has_keywords:
                # Record for qualified keyword access (e.g. typing.Optional)
                imports[module_name] = None
            if not has_keywords or PARSER_KEYWORDS[module_name] is not None:
                # Module may have .py file -- track for file resolution.
                # Skipped only for modules where ALL names are keywords (None).
                user_module_imports[module_name] = node.lineno
                bare_module_imports.add(module_name)
                if module_name not in imports:
                    imports[module_name] = set()
                if local_name != module_name:
                    module_aliases[module_name] = local_name
                if not has_keywords:
                    if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                        import_alias = local_name if local_name != module_name else None
                        top_level_stmts.append(TpyImport(module_name=module_name, alias=import_alias, loc=SourceLocation(node.lineno, node.col_offset)))
            elif local_name != module_name:
                module_aliases[module_name] = local_name

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
                    raise ParseError("'from ... import *' not supported for relative imports", node)
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

        has_keywords = is_parser_keyword_module(module_name)

        # tpy has special star-import and alias tracking
        if module_name == "tpy":
            if any(alias.name == "*" for alias in node.names):
                exports = get_tpy_exports()
                imports["tpy"] = {(name, name) for name in exports}
                for name in exports:
                    self._name_index[name] = ("tpy", name)
                self.tpy_star_import = True
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

        # Track all imported names in the imports dict
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        has_non_keyword = False
        if current is not None:
            for alias in node.names:
                if alias.name == "*":
                    raise ParseError(f"'from {module_name} import *' not supported", node)
                local_name = alias.asname if alias.asname else alias.name
                current.add((alias.name, local_name))
                self._index_import(module_name, alias.name, local_name)
                if not is_parser_keyword(module_name, alias.name):
                    has_non_keyword = True

        # If any imported name is not a parser keyword, trigger file resolution.
        # For non-keyword modules, all names trigger file resolution.
        if not has_keywords or has_non_keyword:
            user_module_imports[module_name] = node.lineno
            if not has_keywords:
                # Pure user module -- emit TpyImport for __tpy_init() ordering.
                # Parser-keyword modules get TpyImport injected by the compiler
                # only when a .py file is actually found (avoids dead source comments).
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    top_level_stmts.append(TpyImport(module_name=module_name, loc=SourceLocation(node.lineno, node.col_offset)))
