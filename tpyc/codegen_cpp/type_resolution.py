"""Shared type-resolution helpers for codegen."""

from __future__ import annotations

from ..typesys import PendingViewType, TpyType, unwrap_readonly
from ..sema.context import PENDING_CONTAINER_TYPES
from ..sema import SemanticAnalyzer
from ..parse import TpyVarDecl
from .types import TypeResolver

# Types that indicate an unresolved intermediate sema state.
_PENDING_TYPES = (*PENDING_CONTAINER_TYPES, PendingViewType)


def resolve_stmt_binding_type(
    stmt: TpyVarDecl,
    analyzer: SemanticAnalyzer,
    include_global_binding: bool = True,
) -> TpyType | None:
    """Resolve variable type via var_types -> global binding.

    Keeps lookup logic consistent across codegen paths and skips unresolved
    pending types from intermediate sema state.
    """
    var_type = stmt.type
    if var_type is not None or stmt.init is None:
        return var_type

    var_type = analyzer.var_types.get(stmt)
    if var_type is None or isinstance(var_type, _PENDING_TYPES):
        if include_global_binding:
            binding = analyzer.global_ns.lookup_local(stmt.name)
            if binding and binding.type is not None:
                var_type = binding.type
    return var_type


def resolve_stmt_type_cascade(
    stmt: TpyVarDecl,
    analyzer: SemanticAnalyzer,
    types: TypeResolver,
    include_global_binding: bool = True,
) -> TpyType | None:
    """Resolve variable type via var_types -> global binding -> expr type."""
    var_type = resolve_stmt_binding_type(
        stmt,
        analyzer,
        include_global_binding=include_global_binding,
    )
    if var_type is None or isinstance(var_type, _PENDING_TYPES):
        assert stmt.init is not None
        var_type = types.get_resolved_type(stmt.init)
    return var_type


def resolve_stmt_recorded_type(
    stmt: TpyVarDecl,
    analyzer: SemanticAnalyzer,
    include_global_binding: bool = True,
) -> TpyType | None:
    """Resolve variable type via var_types -> global binding -> the type sema
    recorded for the initializer.

    The TypeResolver-free twin of `resolve_stmt_type_cascade`, for callers that
    hold only the analyzer. `None` means sema recorded no type at any of the
    three, so nothing can spell the slot -- the one condition both twins have
    to read the same way, or one rejects a binding the other happily
    mis-emits.
    """
    var_type = resolve_stmt_binding_type(
        stmt,
        analyzer,
        include_global_binding=include_global_binding,
    )
    if stmt.init is None:
        return var_type
    if var_type is None or isinstance(var_type, _PENDING_TYPES):
        init_type = analyzer.get_expr_type(stmt.init)
        # Readonly is a sema-side annotation with no bearing on the C++ slot
        # type; stripping it here matches what the codegen context hands back.
        var_type = unwrap_readonly(init_type) if init_type is not None else None
    return var_type
