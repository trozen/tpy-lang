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
# Only type constructors without .py definitions remain as parser keywords.
PARSER_KEYWORDS: dict[str, frozenset[str] | None] = {
    # None means ALL names are parser keywords (module cannot be shadowed by .py)
    "tpy": frozenset({
        # Type constructors with special parser handling (no .py definitions)
        "Own", "Fn",
        # Decorators/type modifiers (consumed at parse time, not .py-defined types)
        "readonly", "noalloc", "nocopy", "dynamic", "pure",
        "auto_readonly", "auto_own", "error_return",
    }),
    # tpy.extern is NOT listed here to avoid changing import processing for user code.
    # Extern keyword names are checked via _EXTERN_KEYWORDS in is_parser_keyword().
    "builtins": None,
    "__future__": None,  # no-op, never resolved as .py
    "typing": frozenset({"Optional", "Final", "Callable"}),
}

# Extern decorator names: parser keywords from tpy.extern.
# Kept separate from PARSER_KEYWORDS to avoid changing import processing
# behavior for user code that imports from tpy.extern.
_EXTERN_KEYWORDS = frozenset({
    "native", "native_c", "extern_c", "cpp_template",
    "builtin_type", "value_ptr_coercion", "native_preserves_refs",
})

# Private submodule -> public module name overrides.
# Used when public_module_name() can't derive the correct public name
# (e.g. tpy._core._extern maps to tpy.extern, not tpy).
_PRIVATE_MODULE_PUBLIC_NAMES: dict[str, str] = {
    "tpy._core._extern": "tpy.extern",
    "tpy._core._typing": "typing",
}

# Types from tpy that require explicit import (used for error messages)
TPY_TYPES = {
    "Int8", "Int16", "Int32", "Int64",  # Signed fixed-width integers
    "UInt8", "UInt16", "UInt32", "UInt64",  # Unsigned fixed-width integers
    "Float32", "Float64",  # Explicit-width float types
    "Char",  # Character type
    "Span", "Array",  # Container types
    "Ptr", "Own",  # Pointer types
    "Fn",  # Callable types
    "Hashable", "Comparable", "Deref", "Default",  # Protocols (user-facing)
    "Truthy", "Stringable", "Representable",  # Protocols (less common)
}

# Python builtins -- always available without import
PYTHON_BUILTINS = frozenset({"int", "float", "bool", "str", "None", "tuple", "slice", "Exception", "BaseException"})

# Names from typing that require explicit import
TYPING_NAMES = frozenset({"Optional", "Protocol", "Self", "Sized", "Sequence", "MutableSequence", "Iterator", "Iterable", "Final", "override", "overload", "Callable"})

# All tpy type names available via `from tpy import *` (types + decorators/modifiers)
TPY_TYPE_NAMES = TPY_TYPES | {
    "String", "StrView", "SpanIter",
    "readonly", "noalloc", "nocopy", "dynamic", "pure",
    "auto_readonly", "auto_own", "error_return",
}


def is_parser_keyword_module(module_name: str) -> bool:
    """Check if a module has any parser keyword handling."""
    return module_name in PARSER_KEYWORDS


def is_parser_keyword(module_name: str, name: str) -> bool:
    """Check if a specific name from a module is a parser keyword.

    Returns True if the name must be handled by the parser (not from .py files).
    For modules with PARSER_KEYWORDS[mod] = None, ALL names are keywords.
    For private submodules (e.g. tpy._core._decorators), checks the public parent.
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

    def __init__(self, warn_fn, module_name: str | None = None):
        self._warn = warn_fn
        self._module_name = module_name
        self.tpy_import_aliases: dict[str, str] = {}
        self.tpy_star_import: bool = False
        # Reference to the module's imports dict, set during process_import_from.
        # Used by the parser for type resolution of imported builtin submodule types.
        self.imports: dict[str, set[tuple[str, str]] | None | str] | None = None

    def _resolve_placeholder_module(self, key: str) -> str:
        """Resolve a relative import placeholder to a public module name.

        Uses the current module_name to compute the absolute path, then
        applies public_module_name to map private submodules to public parents.
        Falls back to the raw key if module_name is not set.
        """
        if not self._module_name:
            return key
        decoded = RelativeImportKey.decode(key)
        parts = self._module_name.split(".")
        # Go up 'level' directories from current module
        if decoded.level > len(parts):
            return key
        parent_parts = parts[:-decoded.level]
        if decoded.partial:
            absolute = ".".join(parent_parts + [decoded.partial])
        else:
            absolute = ".".join(parent_parts)
        return _PRIVATE_MODULE_PUBLIC_NAMES.get(absolute) or public_module_name(absolute)

    def get_import_source(self, local_name: str) -> tuple[str, str] | None:
        """Find source module and original name for an imported name.

        Returns (module_name, original_name) or None.
        For relative imports from private submodules, the module name is
        resolved to the public parent (e.g. tpy._core._types -> tpy).
        """
        if self.imports is None:
            return None
        for module_name, names in self.imports.items():
            if isinstance(names, set):
                for original, local in names:
                    if local == local_name:
                        if RelativeImportKey.is_placeholder(module_name):
                            resolved = self._resolve_placeholder_module(module_name)
                            return (resolved, original)
                        return (module_name, original)
        return None

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
                imports["tpy"] = "*"
                self.tpy_star_import = True
                return
            if "tpy" not in imports:
                imports["tpy"] = set()
            current = imports["tpy"]
            if current is not None and current != "*":
                for alias in node.names:
                    original_name = alias.name
                    local_name = alias.asname if alias.asname else alias.name
                    current.add((original_name, local_name))
                    self.tpy_import_aliases[local_name] = original_name
            return

        # Track all imported names in the imports dict
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        has_non_keyword = False
        if current is not None and current != "*":
            for alias in node.names:
                if alias.name == "*":
                    raise ParseError(f"'from {module_name} import *' not supported", node)
                local_name = alias.asname if alias.asname else alias.name
                current.add((alias.name, local_name))
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
