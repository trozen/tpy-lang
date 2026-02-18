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


# Modules with special parser handling (not resolved as user files)
# tpy: type imports, __future__: ignored, typing: type hints, builtins: always available
# Note: math, time, sys can be shadowed by user files and are NOT in this set
SPECIAL_MODULES = {"tpy", "__future__", "typing", "builtins"}

# Types from tpy that require explicit import (not auto-available like Python builtins)
# Python builtins (int, str, bool, list, float, None) remain auto-available
TPY_TYPES = {
    "Int8", "Int16", "Int32", "Int64",  # Signed fixed-width integers
    "UInt8", "UInt16", "UInt32", "UInt64",  # Unsigned fixed-width integers
    "Char",  # Character type
    "Span", "Array", "StaticList",  # Container types
    "Ptr", "ConstPtr", "Own",  # Pointer types
}


def check_tpy_type_imported(
    name: str, resolved_name: str, node: ast.AST,
    tpy_star_import: bool, tpy_import_aliases: dict[str, str],
) -> None:
    """Check that a tpy type was explicitly imported before use."""
    if resolved_name not in TPY_TYPES:
        return
    if tpy_star_import:
        return
    if name in tpy_import_aliases:
        return
    raise ParseError(
        f"'{name}' is not defined. Did you mean: from tpy import {resolved_name}",
        node
    )


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
            # Check if it's a user module (not builtin)
            if module_name not in SPECIAL_MODULES:
                # User module import - track line number and add to statements
                user_module_imports[module_name] = node.lineno
                bare_module_imports.add(module_name)
                if module_name not in imports:
                    imports[module_name] = set()
                # Track alias if different from module name
                if local_name != module_name:
                    module_aliases[module_name] = local_name
                # Add TpyImport statement (only first time we see this module)
                if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                    import_alias = local_name if local_name != module_name else None
                    top_level_stmts.append(TpyImport(module_name=module_name, alias=import_alias, loc=SourceLocation(node.lineno, node.col_offset)))
                continue
            # 'import X' or 'import X as Y' -> module_name: None (whole module imported)
            imports[module_name] = None
            if local_name != module_name:
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

        # Check if it's a user module (not builtin)
        if module_name not in SPECIAL_MODULES:
            # User module import: from utils import add, Point - track line number
            user_module_imports[module_name] = node.lineno
            if module_name not in imports or imports[module_name] is None:
                imports[module_name] = set()
            current = imports[module_name]
            if current != "*":
                for alias in node.names:
                    if alias.name == "*":
                        raise ParseError(f"'from {module_name} import *' not supported for user modules", node)
                    local_name = alias.asname if alias.asname else alias.name
                    current.add((alias.name, local_name))
            # Add TpyImport statement (only first time we see this module)
            if not any(isinstance(s, TpyImport) and s.module_name == module_name for s in top_level_stmts):
                top_level_stmts.append(TpyImport(module_name=module_name, loc=SourceLocation(node.lineno, node.col_offset)))
            return
        # Skip __future__ imports - they affect CPython parsing but are no-op for TurboPython
        if module_name == "__future__":
            return
        # Track tpy imports like other modules - sema will determine if they're
        # types (Int32) or functions (copy) and handle accordingly
        # Store as (original_name, local_name) tuples to support aliases
        if module_name == "tpy":
            # Handle "from tpy import *" specially
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
                    # Track alias for type annotation resolution
                    self.tpy_import_aliases[local_name] = original_name
            return
        # 'from X import Y, Z' -> module_name: {(original, local), ...}
        # Store as (original_name, local_name) tuples to support aliases
        if module_name not in imports:
            imports[module_name] = set()
        current = imports[module_name]
        if current is not None and current != "*":  # Not overridden by 'import X' or '*'
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                current.add((alias.name, local_name))
