"""Class-macro application phase.

Runs inside `register_record` after field-type validation and before
method expansion.  Two steps:

1. Apply pending class macros to the record.  Macros read resolved
   field types and may add methods via `ClassInfo.add_method` /
   `add_method_from_source`.
2. Resolve any `TypeRefNode`s in macro-added method bodies.  Source
   methods had their bodies resolved by the module-level
   `parse.resolve_refs.resolve_refs` walk; macro-added methods join
   the record after that walk finished and need a late cleanup pass.

Lives on the sema side (and not as a standalone compiler phase)
because the macro API (`ClassInfo`) exposes sema state -- registry
lookups for parent records, method overloads, module iteration -- that
macros like `@dataclass` and `@model` need for type-directed codegen
decisions.  This is the standard shape for type-aware-macro languages
(Zig comptime, Nim typed macros, D __traits, Scala 3 quotes all
interleave macro execution with semantic analysis).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse.nodes import TpyCall, TpyMethodCall
from ..parse.resolve_refs import resolve_method_body_refs, promote_bare_nominals
from ..macro_api import ClassInfo
from ..macro_loader import validate_and_call_macro, call_macro_field_function
from ..diagnostics import SemanticError
from ..symbol_binding import is_macro_kind, walk_attribute_chain


def _resolve_macro_through_attributes(
    ctx: 'SemanticContext', module_name: str, name: str,
) -> tuple[str, str] | None:
    """Walk the binding chain to find the ultimate macro source for
    `(module_name, name)`. Returns None when no hop lands on a macro
    binding -- recovers the macro registry's canonical key when the
    macro flows through re-export chains (the parser stores the
    *immediate* source on `record.pending_macros`).
    """
    result = walk_attribute_chain(ctx.registry, module_name, name, is_macro_kind)
    if result is None:
        return None
    ult_mod, ult_name, _bd = result
    return (ult_mod, ult_name)

if TYPE_CHECKING:
    from ..parse.nodes import TpyRecord
    from ..typesys import TypeRegistry
    from .context import SemanticContext


def run_macro_phase_for_record(record: 'TpyRecord', ctx: 'SemanticContext') -> None:
    """Apply class macros and resolve any TypeRefNodes they left behind.

    No-op when the record carries no pending macros and contains no
    TypeRefNodes in its method bodies; safe to call unconditionally.
    """
    _apply_class_macros(record, ctx)
    _apply_deferred_class_macros(record, ctx)
    _resolve_macro_added_method_bodies(record, ctx)


def _apply_class_macros(record: 'TpyRecord', ctx: 'SemanticContext') -> None:
    """Apply pending class macros to a record.

    Macros may read resolved field types (set by `register_record`'s
    field validation loop before this runs) and mutate the record via
    `ClassInfo.add_method` / `add_field` / `set_match_args` / etc.
    """
    if not record.pending_macros:
        return
    registry = ctx.macro_registry
    # Reverse so innermost decorator runs first (`outer(inner(C))` semantics).
    for qname, kwargs in reversed(record.pending_macros):
        parts = qname.rsplit(".", 1)
        if len(parts) != 2:
            raise SemanticError(f"Invalid macro name '{qname}'", record.loc)
        mod_name, func_name = parts
        macro_fn = registry.get_macro(mod_name, func_name) if registry else None
        if macro_fn is None:
            # Phase 6: re-export chain. The parser may have resolved
            # the decorator's source module to an intermediate that
            # itself imports the macro (e.g. `utils` re-exporting
            # `dataclass` from `dataclasses`). Walk the binding chain
            # in `ctx.module_attributes` to find the ultimate macro
            # source.
            ult = _resolve_macro_through_attributes(ctx, mod_name, func_name)
            if ult is not None:
                ult_mod, ult_name = ult
                macro_fn = registry.get_macro(ult_mod, ult_name) if registry else None
                if macro_fn is not None:
                    qname = f"{ult_mod}.{ult_name}"
        if macro_fn is None:
            raise SemanticError(f"Unknown macro '{qname}'", record.loc)
        cls_info = ClassInfo(record, ctx, macro_origin=func_name)
        # Call macro-module functions in field defaults (e.g. `field()` -> Field).
        for fld in cls_info.fields:
            if fld.default_expr is not None and isinstance(fld.default_expr, (TpyCall, TpyMethodCall)):
                result = call_macro_field_function(
                    registry, fld.default_expr, fld.loc)
                if result is not None:
                    fld.default_obj = result
        validate_and_call_macro(macro_fn, cls_info, kwargs, qname, record.loc)
        cls_info.apply_to_record()
        record._macro_cls_info = cls_info  # type: ignore[attr-defined]


def _apply_deferred_class_macros(
    record: 'TpyRecord', ctx: 'SemanticContext',
) -> None:
    """Run callbacks registered via `ClassInfo.defer_until_macros_complete`.

    Drains the list before iterating so a deferred callback that
    re-registers (rare; would imply a self-recursive macro) raises
    rather than silently looping. Callbacks see the fully-composed
    method set produced by the eager pass.
    """
    if not record.pending_deferred_macros:
        return
    from ..macro_api import MacroError
    callbacks = list(record.pending_deferred_macros)
    record.pending_deferred_macros.clear()
    for origin, callback in callbacks:
        cls_info = ClassInfo(record, ctx, macro_origin=origin)
        try:
            callback(cls_info)
        except MacroError as e:
            raise SemanticError(str(e), e.loc or record.loc) from e
        cls_info.apply_to_record()
        if record.pending_deferred_macros:
            raise SemanticError(
                "deferred class macro registered another deferred callback "
                "(not supported)", record.loc,
            )


def _resolve_macro_added_method_bodies(record: 'TpyRecord', ctx: 'SemanticContext') -> None:
    """Resolve `TypeRefNode`s in bodies of methods added by macros.

    Macros that call `add_method_from_source(...)` produce method
    bodies with TpyTypeRef annotations (FragmentParser's walker leaves
    them unresolved).  This pass resolves them against the enclosing
    module's resolver so downstream sema sees TpyType everywhere.

    Signature-level bare `NominalType` placeholders emitted by
    `types.named(...)` are NOT promoted here -- that happens in
    `analyzer._promote_macro_generated_types` after
    `_populate_macro_deps` has made cross-module macro dependencies
    (e.g. JsonReader, JsonWriter) visible in `ctx.registry`.  Doing
    it here would only catch same-module refs and leave cross-module
    ones to the second pass anyway.

    Idempotent on source-defined methods whose bodies are already
    fully resolved.
    """
    resolver = ctx.parser_resolver
    if resolver is None:
        return
    for method in record.methods:
        resolve_method_body_refs(method, record, resolver)


def _promote_method_signature(method, registry: 'TypeRegistry') -> None:
    """Promote bare NominalType placeholders in a method's signature to
    qname-bearing form, in place."""
    method.params = [
        (name, promote_bare_nominals(t, registry)) for name, t in method.params
    ]
    if method.return_type is not None:
        method.return_type = promote_bare_nominals(method.return_type, registry)
    if method.vararg_type is not None:
        method.vararg_type = promote_bare_nominals(method.vararg_type, registry)
    if method.kwarg_type is not None:
        method.kwarg_type = promote_bare_nominals(method.kwarg_type, registry)
    if method.self_annotation is not None:
        method.self_annotation = promote_bare_nominals(method.self_annotation, registry)
