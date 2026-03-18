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


# Names that the parser must resolve during parsing (compiler intrinsics).
# Other names from these modules can come from .py files via normal module resolution.
# Modules not listed here are always resolved as user files.
PARSER_KEYWORDS: dict[str, frozenset[str] | None] = {
    # None means ALL names are parser keywords (module cannot be shadowed by .py)
    "tpy": frozenset({
        # Types used in annotations at parse time
        "Int8", "Int16", "Int32", "Int64",
        "UInt8", "UInt16", "UInt32", "UInt64",
        "Float32", "Float64",
        "Array", "Span", "SpanIter", "Ptr", "Own", "Char",
        "String", "StrView", "ReadOnlyPtr",
        # Callable types
        "Fn",
        # Decorators/modifiers
        "readonly", "noalloc", "nocopy", "dynamic", "pure",
        "auto_readonly", "error_return",
    }),
    "builtins": None,
    "__future__": None,  # no-op, never resolved as .py
    "typing": frozenset({"Protocol", "Optional", "Final", "overload", "override", "Self"}),
    "enum": frozenset({"Enum", "IntEnum", "auto"}),
    "dataclasses": frozenset({"dataclass", "field"}),
}

# Types from tpy that require explicit import (not auto-available like Python builtins)
# Python builtins (int, str, bool, list, float, None) remain auto-available
TPY_TYPES = {
    "Int8", "Int16", "Int32", "Int64",  # Signed fixed-width integers
    "UInt8", "UInt16", "UInt32", "UInt64",  # Unsigned fixed-width integers
    "Float32", "Float64",  # Explicit-width float types
    "Char",  # Character type
    "Span", "Array",  # Container types
    "Ptr", "ReadOnlyPtr", "Own",  # Pointer types (ReadOnlyPtr is a deprecated alias for Ptr[readonly[T]])
    "Fn",  # Callable types
    "Hashable", "Comparable", "Deref", "Default",  # Protocols (user-facing)
    "Truthy", "Stringable", "Representable",  # Protocols (less common)
}

# Python builtins -- always available without import
PYTHON_BUILTINS = frozenset({"int", "float", "bool", "str", "None", "tuple", "slice", "Exception", "BaseException"})

# Names from typing that require explicit import
TYPING_NAMES = frozenset({"Optional", "Protocol", "Self", "Sized", "Sequence", "MutableSequence", "Iterator", "Iterable", "Final", "override", "overload"})

# All tpy type names (union of TPY_TYPES + decorators/modifiers)
TPY_TYPE_NAMES = TPY_TYPES | {"Char", "readonly", "noalloc", "nocopy", "dynamic", "pure", "auto_readonly", "error_return"}


def is_parser_keyword_module(module_name: str) -> bool:
    """Check if a module has any parser keyword handling."""
    return module_name in PARSER_KEYWORDS


def is_parser_keyword(module_name: str, name: str) -> bool:
    """Check if a specific name from a module is a parser keyword.

    Returns True if the name must be handled by the parser (not from .py files).
    For modules with PARSER_KEYWORDS[mod] = None, ALL names are keywords.
    """
    kw = PARSER_KEYWORDS.get(module_name)
    if kw is None and module_name in PARSER_KEYWORDS:
        return True  # None means all names are keywords
    if kw is not None:
        return name in kw
    return False


class ImportProcessor:
    """Processes and tracks import statements during parsing.

    Separates import validation and tracking (semantic concerns)
    from pure syntax parsing.
    """

    def __init__(self, warn_fn):
        self._warn = warn_fn
        self.tpy_import_aliases: dict[str, str] = {}
        self.tpy_star_import: bool = False
        # Reference to the module's imports dict, set during process_import_from.
        # Used by the parser for type resolution of imported builtin submodule types.
        self.imports: dict[str, set[tuple[str, str]] | None | str] | None = None

    def get_import_source(self, local_name: str) -> tuple[str, str] | None:
        """Find source module and original name for an imported name.

        Returns (module_name, original_name) or None.
        E.g. for 'from tpy.mem import UninitArrayStorage as U':
            get_import_source('U') -> ('tpy.mem', 'UninitArrayStorage')
        """
        if self.imports is None:
            return None
        for module_name, names in self.imports.items():
            if isinstance(names, set):
                for original, local in names:
                    if local == local_name:
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
