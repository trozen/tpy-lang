"""Shared type-resolution helpers for codegen."""

from __future__ import annotations

from ..typesys import PendingListType, PendingDictType, PendingStrType, TpyType
from ..parse import TpyVarDecl


def resolve_stmt_binding_type(
    stmt: TpyVarDecl,
    analyzer: object,
    include_global_binding: bool = True,
) -> TpyType | None:
    """Resolve variable type via var_types -> global binding.

    Keeps lookup logic consistent across codegen paths and skips unresolved
    PendingListType entries from intermediate sema state.
    """
    var_type = stmt.type
    if var_type is not None or stmt.init is None:
        return var_type

    var_type = analyzer.var_types.get(id(stmt))
    if var_type is None or isinstance(var_type, (PendingListType, PendingDictType, PendingStrType)):
        if include_global_binding:
            binding = analyzer.global_ns.lookup_local(stmt.name)
            if binding and binding.type is not None:
                var_type = binding.type
    return var_type


def resolve_stmt_type_cascade(
    stmt: TpyVarDecl,
    analyzer: object,
    types: object,
    include_global_binding: bool = True,
) -> TpyType | None:
    """Resolve variable type via var_types -> global binding -> expr type."""
    var_type = resolve_stmt_binding_type(
        stmt,
        analyzer,
        include_global_binding=include_global_binding,
    )
    if var_type is None or isinstance(var_type, (PendingListType, PendingDictType, PendingStrType)):
        var_type = types.get_resolved_type(stmt.init)
    return var_type
