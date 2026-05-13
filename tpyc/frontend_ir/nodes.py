"""Frontend IR node definitions.

Plain dataclasses with `kind` discriminators. No methods, no behavior --
the IR is structural data that lowering walks.

M1 set: the nodes needed for a Pascal hello-world program. Subsequent
milestones extend this set without changing existing shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


API_VERSION = 1


@dataclass(frozen=True)
class Loc:
    """Source location in the original (non-Python) source file.

    Line and column are 1-based; end_line/end_col are inclusive. `file`
    is the path of the source file the plugin parsed. `source_language`
    tags the plugin that produced this location.
    """
    file: Path
    line: int
    col: int
    end_line: int
    end_col: int
    source_language: str


# --- Expressions ----------------------------------------------------------


@dataclass
class StrLit:
    """String literal expression."""
    kind: str = field(default="StrLit", init=False)
    value: str = ""
    loc: Loc | None = None


@dataclass
class Name:
    """Identifier reference."""
    kind: str = field(default="Name", init=False)
    ident: str = ""
    loc: Loc | None = None


@dataclass
class Call:
    """Function call.

    M1 form is positional-only. `type_args`, `kwargs`, `star_args`,
    `double_star` from the design doc are not used yet; they land with
    later milestones.
    """
    kind: str = field(default="Call", init=False)
    callee: "Expr" = None  # type: ignore[assignment]
    args: tuple["Expr", ...] = ()
    loc: Loc | None = None


Expr = Union[StrLit, Name, Call]


# --- Statements -----------------------------------------------------------


@dataclass
class ExprStmt:
    """Expression statement (the expression's value is discarded)."""
    kind: str = field(default="ExprStmt", init=False)
    value: Expr = None  # type: ignore[assignment]
    loc: Loc | None = None


Stmt = ExprStmt  # M1: ExprStmt is the only Stmt; expand as milestones land.


# --- Imports --------------------------------------------------------------


@dataclass
class ImportName:
    """A name imported via `from X import Y as Z`. `local` equals
    `original` when there is no `as` clause."""
    original: str
    local: str


@dataclass
class Import:
    """`import M [as A]`."""
    kind: str = field(default="Import", init=False)
    module: str = ""
    alias: str | None = None
    loc: Loc | None = None


@dataclass
class FromImport:
    """`from M import a [as b], c, ...`."""
    kind: str = field(default="FromImport", init=False)
    module: str = ""
    names: tuple[ImportName, ...] = ()
    loc: Loc | None = None


ImportDecl = Union[Import, FromImport]


# --- Directives -----------------------------------------------------------


@dataclass
class FrontendDirectives:
    """Module-level directives.

    Mirrors `tpyc.parse.nodes.ModuleDirectives`. M1 leaves everything at
    its default; only relevant when a plugin emits native C++ includes,
    link libs, or marks itself as a native binding module.
    """
    cpp_includes: tuple[tuple[str, str | None], ...] = ()  # (header, platform_filter)
    link_libs: tuple[tuple[str, str | None], ...] = ()
    third_party_deps: tuple[tuple[str, str | None], ...] = ()
    native_module: bool = False
    cpp_namespace: str | None = None


# --- Module ---------------------------------------------------------------


@dataclass
class FrontendModule:
    """A frontend IR module.

    The plugin's `parse()` returns one of these. Lowering converts it
    into a `tpyc.parse.nodes.TpyModule`.

    M1 carries only top-level statements + imports. Records, enums,
    functions, type aliases, constants are typed as empty tuples now
    and populated by later milestones.
    """
    api_version: int = API_VERSION
    qname: str = ""
    source_language: str = ""
    source_lines: tuple[str, ...] = ()
    imports: tuple[ImportDecl, ...] = ()
    type_aliases: tuple = ()
    constants: tuple = ()
    enums: tuple = ()
    records: tuple = ()
    functions: tuple = ()
    top_level_stmts: tuple[Stmt, ...] = ()
    directives: FrontendDirectives = field(default_factory=FrontendDirectives)
