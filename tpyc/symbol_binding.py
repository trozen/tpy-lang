"""Per-module attribute table.

A single `dict[str, BindingCell]` per module, indexed by short local
name. Each cell holds a `SymbolBinding` describing what the name
refers to: kind (function / record / protocol / ...), the canonical
Info object, and the (defining_module, canonical_name) pair the
codegen `qualify_imported()` helper turns into a C++ qname.

Cells are mutable indirections so peer references captured during
pre-population stay live as the binding's payload Info object is
filled in by the registration paths. The binding itself is frozen --
identity refinement, when needed, happens by rebinding the cell's
`.binding` slot.

See docs/MUTUAL_IMPORTS_DESIGN.md "Universal re-export via per-module
attribute table" for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SymbolKind(Enum):
    FUNCTION = "function"
    RECORD = "record"
    PROTOCOL_STATIC = "protocol_static"
    PROTOCOL_DYNAMIC = "protocol_dynamic"
    ENUM = "enum"
    VARIABLE = "variable"
    TYPE_ALIAS = "type_alias"
    MODULE = "module"
    SUBMODULE = "submodule"
    CLASS_MACRO = "class_macro"
    CALL_MACRO = "call_macro"
    BUILDER_MACRO = "builder_macro"
    PARSER_KEYWORD = "parser_keyword"
    OPAQUE = "opaque"


@dataclass(frozen=True)
class SymbolBinding:
    """One name in a module's attribute table.

    `info` holds the canonical Info object for the binding's kind:
      FUNCTION              -> list[FunctionInfo]
      RECORD                -> RecordInfo
      PROTOCOL_*            -> ProtocolInfo
      ENUM                  -> NominalType (enum-kind)
      VARIABLE              -> TpyType (var's declared type)
      TYPE_ALIAS            -> TpyType (alias body)
      MODULE / SUBMODULE    -> ModuleInfo (or None for special modules)
      CLASS_MACRO / ...     -> macro registration object
      PARSER_KEYWORD        -> None
      OPAQUE                -> None

    `defining_module` is None when the symbol is locally defined in the
    module that owns this binding. For imports/re-exports it's the
    *ultimate* defining module (chain-flattened): records use
    `record_info.defining_module`, enums use `EnumInfo.module_name`,
    functions use `FunctionInfo.originating_module`, variables use
    the second pre-pop pass's chain-walk.

    `canonical_name` is the symbol's name in the defining module
    (== local_name when no aliasing).
    """
    local_name: str
    kind: SymbolKind
    info: object
    defining_module: str | None
    canonical_name: str


@dataclass
class BindingCell:
    """Mutable indirection wrapping a `SymbolBinding`.

    Cells are the stable references peers capture during
    `bind_imports`; the cell's `.binding` slot may be rebound when
    the same name is re-installed (e.g. a local definition shadowing
    an earlier import in source order, or a registration path
    refining a pre-pop'd skeleton). The binding objects themselves
    stay frozen.
    """
    binding: SymbolBinding


def make_binding(
    local_name: str,
    kind: SymbolKind,
    info: object,
    *,
    defining_module: str | None = None,
    canonical_name: str | None = None,
) -> SymbolBinding:
    return SymbolBinding(
        local_name=local_name,
        kind=kind,
        info=info,
        defining_module=defining_module,
        canonical_name=canonical_name if canonical_name is not None else local_name,
    )


def install_binding(
    table: 'dict[str, BindingCell] | None',
    local_name: str,
    kind: SymbolKind,
    info: object,
    *,
    defining_module: str | None = None,
    canonical_name: str | None = None,
) -> None:
    """Install or replace an entry in the attribute table.

    No-op when `table` is None (some analyzer constructions don't have
    one -- e.g. ad-hoc tests / REPL).

    Cells are mutated in place when they already exist, preserving the
    cell identity peer modules may have captured during `bind_imports`.
    """
    if table is None:
        return
    binding = make_binding(
        local_name, kind, info,
        defining_module=defining_module,
        canonical_name=canonical_name,
    )
    cell = table.get(local_name)
    if cell is None:
        table[local_name] = BindingCell(binding=binding)
    else:
        cell.binding = binding


def protocol_kind_for(is_dynamic: bool) -> SymbolKind:
    return SymbolKind.PROTOCOL_DYNAMIC if is_dynamic else SymbolKind.PROTOCOL_STATIC


def qualify_imported(binding: SymbolBinding, current_module: str) -> tuple[str, str]:
    """Return the (module, canonical_name) pair to use when emitting a
    use-site reference to `binding` from inside `current_module`.

    The result is the input to `qualified_cpp_name(...)`; callers that
    want the rendered C++ string take that extra step so this helper
    stays free of the codegen-context import.
    """
    if binding.defining_module is None:
        return (current_module, binding.canonical_name)
    return (binding.defining_module, binding.canonical_name)


def lookup_qualified(
    table: 'dict[str, BindingCell] | None', name: str, current_module: str,
) -> 'tuple[str, str] | None':
    """Convenience for the common codegen pattern: fetch the cell from
    `table` and pass its binding through `qualify_imported`. Returns
    None when the table is missing or the name isn't bound, leaving
    the caller's fallback path in charge.
    """
    if table is None:
        return None
    cell = table.get(name)
    if cell is None:
        return None
    return qualify_imported(cell.binding, current_module)


def lookup_imported(
    table: 'dict[str, BindingCell] | None', name: str, *kinds: SymbolKind,
) -> 'tuple[str, str] | None':
    """Return `(defining_module, canonical_name)` when `name` is bound as
    an *imported* symbol of one of `kinds` -- i.e. the cell exists, kind
    matches, and the binding has a non-None `defining_module`. Returns
    None for local definitions, kind mismatches, missing entries, and a
    None table.
    """
    if table is None:
        return None
    cell = table.get(name)
    if cell is None:
        return None
    bd = cell.binding
    if bd.defining_module is None:
        return None
    if kinds and bd.kind not in kinds:
        return None
    return (bd.defining_module, bd.canonical_name)


def resolve_definer(
    registry, module_name: str, name: str, *kinds: SymbolKind,
) -> tuple[str, str]:
    """Return the (definer_module, definer_name) for `name` as bound in
    `module_name`'s attribute table, or `(module_name, name)` if the
    cell is missing / locally-defined / kind-mismatched.

    `registry` only needs `get_module(name) -> object | None`; the
    returned object only needs a `module_attributes` attribute that's
    a `dict | None`. Works equally with the sema `TypeRegistry` and the
    `Compiler`'s `modules` dict via a thin adapter.
    """
    mi = registry.get_module(module_name)
    if mi is None:
        return (module_name, name)
    table = getattr(mi, "module_attributes", None)
    imp = lookup_imported(table, name, *kinds)
    if imp is None:
        return (module_name, name)
    return imp


def walk_attribute_chain(
    registry, module_name: str, name: str,
    accept,  # Callable[[SymbolBinding], bool]
) -> 'tuple[str, str, SymbolBinding] | None':
    """Walk the `module_attributes` chain across `registry`'s modules
    until `accept(binding)` returns True or the chain dead-ends.

    Returns `(defining_module_or_current, canonical_name, binding)` for
    the accepted cell, or None when no hop matches. `registry` only
    needs `get_module(name) -> ModuleInfo | None` -- works equally with
    `TypeRegistry` (sema) and `Compiler` (with a wrapper).
    """
    visited: set[tuple[str, str]] = set()
    cur_mod, cur_name = module_name, name
    while True:
        key = (cur_mod, cur_name)
        if key in visited:
            return None
        visited.add(key)
        mi = registry.get_module(cur_mod)
        if mi is None or mi.module_attributes is None:
            return None
        cell = mi.module_attributes.get(cur_name)
        if cell is None:
            return None
        bd = cell.binding
        if accept(bd):
            return (bd.defining_module or cur_mod, bd.canonical_name, bd)
        if bd.defining_module is None:
            return None
        cur_mod, cur_name = bd.defining_module, bd.canonical_name


_MACRO_KINDS = frozenset({
    SymbolKind.CLASS_MACRO, SymbolKind.CALL_MACRO, SymbolKind.BUILDER_MACRO,
})


def is_macro_kind(binding: SymbolBinding) -> bool:
    return binding.kind in _MACRO_KINDS


def is_kind(*kinds: SymbolKind):
    """Return an `accept` predicate matching any of `kinds`."""
    s = frozenset(kinds)
    return lambda bd: bd.kind in s
