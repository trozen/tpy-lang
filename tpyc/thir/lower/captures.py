"""Complete bounded capture facts beside the selected closure construction."""

from typing import TYPE_CHECKING

from ...identity_map import IdentityMap
from ...parse.nodes import (
    TpyAssign, TpyDictComprehension, TpyExpr, TpyFieldAccess, TpyFunction, TpyGeneratorExpression,
    TpyLambda, TpyListComprehension, TpyNestedDef, TpySetComprehension,
    TpyStmt, TpyVarDecl,
)
from ...typesys import TpyType, unwrap_readonly, unwrap_ref_type
from ..scalar_leaves import storage_leaf
from ..nodes import (
    THIRCapture, THIRCaptureRelation, THIRCaptureSlot, THIRCaptureSourceKind,
    THIRClosureIdentity, THIRClosureKind,
)
from .predicates import _param_is_const
from .storage import borrowed_record

if TYPE_CHECKING:
    from .context import _LowerCtx


Closure = TpyLambda | TpyNestedDef


class CaptureSites:
    def __init__(self, func: TpyFunction, *, eligible: bool) -> None:
        self.constructor = func.name == "__init__" and func.is_method
        self.sites: IdentityMap[Closure, tuple[THIRClosureIdentity, bool]] = IdentityMap()
        self.entry_locals: set[str] = set()
        for stmt in func.body:
            if stmt.sub_bodies():
                break
            if isinstance(stmt, TpyVarDecl) and stmt.init is not None and not stmt.is_final:
                self.entry_locals.add(stmt.name)
        self._body(func.body, eligible)

    def _body(self, body: list[TpyStmt], eligible: bool) -> bool:
        sites: list[tuple[Closure, bool]] = []

        def expr(e: TpyExpr, allowed: bool, found: list[tuple[Closure, bool]]) -> None:
            match e:
                case TpyLambda():
                    inner: list[tuple[Closure, bool]] = []
                    # The child body has its own occurrence space, even when unavailable.
                    expr(e.body, False, inner)
                    self._number(inner)
                    found.append((e, allowed and not inner))
                case TpyListComprehension() | TpyDictComprehension() | TpySetComprehension() | TpyGeneratorExpression():
                    for child in e.children():
                        expr(child, False, found)
                case _:
                    for child in e.children():
                        expr(child, allowed, found)

        def statements(stmts: list[TpyStmt], allowed: bool) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyNestedDef):
                    nested = self._body(stmt.func.body, False)
                    sites.append((stmt, allowed and not nested
                                  and not stmt.func.is_async and not stmt.func.is_generator))
                else:
                    for e in stmt.exprs():
                        expr(e, allowed and not stmt.sub_bodies() and not (
                            self.constructor and isinstance(stmt, TpyAssign)
                            and isinstance(stmt.target, TpyFieldAccess)), sites)
                    for child in stmt.sub_bodies():
                        statements(child, False)

        statements(body, eligible)
        self._number(sites)
        return bool(sites)

    def _number(self, sites: list[tuple[Closure, bool]]) -> None:
        # Source columns distinguish sites on one line; traversal breaks synthetic ties.
        sites.sort(key=lambda item: (item[0].loc.line, item[0].loc.column)
                   if item[0].loc is not None else (0, 0))
        for index, (node, eligible) in enumerate(sites):
            kind = THIRClosureKind.LAMBDA if isinstance(node, TpyLambda) else THIRClosureKind.NESTED_DEF
            self.sites[node] = (THIRClosureIdentity(index, kind), eligible)


def capture_facts(node: Closure, lc: '_LowerCtx', declared: dict[str, TpyType]
                  ) -> tuple[THIRClosureIdentity | None, tuple[THIRCapture, ...] | None]:
    site = lc.capture_sites.sites.get(node)
    if site is None:
        return None, None
    identity, eligible = site
    if not eligible or lc.capture_funcs:
        return identity, None
    params = dict(lc.params)
    captures: list[THIRCapture] = []
    for name in node.captured_names:
        typ = declared.get(name)
        if (typ is None or name in lc.narrow.narrowed or name in lc.narrow.spelled
                or name in lc.frame_field_names or lc.binding(name).pointer
                or lc.binding(name).rebind_slot or name in lc.branch_hoisted
                or name in lc.nested_def_locals):
            return identity, None
        match node:
            case TpyLambda():
                by_ref = not node.captures_by_value
            case TpyNestedDef():
                if node.escapes and name in node.move_captures:
                    return identity, None
                by_ref = not node.escapes or name in node.ref_captures
        source_kind = (THIRCaptureSourceKind.PARAMETER if name in params
                       else THIRCaptureSourceKind.LOCAL)
        bare = unwrap_readonly(unwrap_ref_type(typ))
        if name == lc.self_receiver:
            if lc.self_cpp != "this" or not lc.self_is_pointer:
                return identity, None
            reference = borrowed_record(typ, lc.func.is_readonly, lc.analyzer)
            if reference is None:
                return identity, None
            source_kind = THIRCaptureSourceKind.RECEIVER
            relation = THIRCaptureRelation.RECEIVER_ALIAS
            bare, readonly = reference.type, reference.readonly
        elif storage_leaf(typ):
            if name not in params and name not in lc.capture_sites.entry_locals:
                return identity, None
            relation = THIRCaptureRelation.SCALAR_BINDING if by_ref else THIRCaptureRelation.SCALAR_SNAPSHOT
            readonly = not by_ref or lc.binding(name).const
        elif name in params and by_ref:
            reference = borrowed_record(typ, _param_is_const(name, lc.func, lc.analyzer, lc.record_name),
                                        lc.analyzer)
            if reference is None:
                return identity, None
            relation = THIRCaptureRelation.RECORD_REFERENT
            bare, readonly = reference.type, reference.readonly
        else:
            return identity, None
        captures.append(THIRCapture(THIRCaptureSlot(identity, len(captures)), name,
                                    source_kind, bare, relation, readonly))
    return identity, tuple(captures)
