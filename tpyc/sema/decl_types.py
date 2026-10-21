"""The type a local declaration binds, as one rule."""

from __future__ import annotations

from typing import Mapping

from ..parse.nodes import TpyVarDecl
from ..typesys import TpyType, peel_binding


def settled_decl_type(stmt: TpyVarDecl, var_types: Mapping) -> object:
    """Declaration `stmt`'s annotation, else the type sema settled for it
    (`var_types`; possibly still pending), else None."""
    if stmt.type is not None or stmt.init is None:
        return stmt.type
    return var_types.get(stmt)


def declared_bound_type(stmt: TpyVarDecl, var_types: Mapping,
                        init_type: TpyType | None) -> TpyType | None:
    """The type declaration `stmt` binds: its settled type
    (`settled_decl_type`), else its initializer's (`init_type`), peeled as
    a binding holds it (`peel_binding`)."""
    t = settled_decl_type(stmt, var_types)
    if t is None and stmt.init is not None:
        t = init_type
    return peel_binding(t) if isinstance(t, TpyType) else None
