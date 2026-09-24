"""Argument, select-slot and inline-constructor backing in a lowered body.

Typed record aliases and selected borrowed returns retain explicit obligations,
including stable-looking names. Other borrowed declarations and PTR_ADDR sinks
retain non-stable values as conservative requirements. The backing inventory
omits storage producers, so it is not an exhaustive admission record.
Argument placement stays the temporary plan's decision; missing
argument placement has an explicit reason. Full-expression backing needs no
temporary-plan placement. These facts carry no source-admission authority.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from types import MappingProxyType

from ..identity_map import IdentityMap
from ..parse import SourceLocation
from . import nodes as th
from .temp_plan import THIRTempPlacement, THIRTempPlan


class THIRBackingKind(Enum):
    # A named declaration the temporary plan places before its statement.
    ARGUMENT = "argument"
    # An optional slot declared before its statement, emplaced by the selected operand.
    SELECT_SLOT = "select_slot"
    # An unnamed temporary destroyed at the end of its full expression.
    FULL_EXPRESSION = "full_expression"


@dataclass(frozen=True)
class THIRBacking:
    index: int
    kind: THIRBackingKind
    node: th.THIRExpr
    # The statement or initializer cell whose full expression evaluates `node`.
    holder: object
    full_expression: th.THIRExpr
    placement: THIRTempPlacement | None
    loc: SourceLocation | None
    # None for planned arguments or non-nested full-expression backing;
    # the latter uses full_expression, not placement. This is not MIR coverage.
    uncovered: str | None


@dataclass(frozen=True)
class THIRBorrowObligation:
    """A borrowed binding, reseat or selected borrowed return.

    The bound referent may depend on any backing in `backings` or on storage
    the facts do not model; an empty tuple is not a proof of stability."""
    index: int
    sink: th.THIRStmt
    value: th.THIRExpr
    backings: tuple[int, ...]


@dataclass(frozen=True)
class THIRStorageFacts:
    body: tuple[th.THIRStmt, ...]
    initializers: tuple[object, ...]
    borrowed_result: th.THIRBorrowedRecord | None
    backings: tuple[THIRBacking, ...]
    obligations: tuple[THIRBorrowObligation, ...]
    by_node: Mapping[th.THIRExpr, THIRBacking]

    def backing(self, node: th.THIRExpr) -> THIRBacking:
        entry = self.by_node.get(node)
        if entry is None or entry.node is not node:
            raise ValueError("foreign materialized storage")
        return entry


_NESTED_BODIES = (th.THIRLambda, th.THIRNestedDef, th.THIRGenExpr)


_FIELD_NAMES: dict[type, tuple[str, ...]] = {}


def _thir_records(value: object, out: list[object]) -> None:
    # Nested tuples included: a folded if-chain holds (condition, body) pairs.
    kind = type(value)
    if kind is tuple or kind is list:
        for item in value:  # type: ignore[attr-defined]
            _thir_records(item, out)
    elif kind.__module__ == th.THIRNode.__module__ and is_dataclass(value):
        out.append(value)


def _children(node: object) -> list[object]:
    names = _FIELD_NAMES.get(type(node))
    if names is None:
        names = _FIELD_NAMES[type(node)] = tuple(member.name for member in fields(node))
    out: list[object] = []
    for name in names:
        _thir_records(getattr(node, name), out)
    return out


def _stable(expr: th.THIRExpr) -> bool:
    match expr:
        case th.THIRName() | th.THIRSelf():
            return True
        case th.THIRFieldAccess():
            return _stable(expr.receiver)
        case th.THIRCoerce():
            return _stable(expr.expr)
        case _:
            return False


def _borrowed_value(stmt: th.THIRStmt,
                    borrowed_result: th.THIRBorrowedRecord | None) -> th.THIRExpr | None:
    match stmt:
        case th.THIRVarDecl() | th.THIRPtrLocalDecl() if (
                stmt.alias_binding is not None or stmt.storage_borrow is not None):
            return stmt.init
        case th.THIRAssign() | th.THIRPtrLocalRebind() if (
                stmt.alias_binding is not None or stmt.storage_borrow is not None):
            return stmt.value
        case th.THIRReturn() if borrowed_result is not None:
            return stmt.value
    match stmt:
        case th.THIRVarDecl() if stmt.form is th.Form.BORROW and stmt.owned_storage is None:
            value = stmt.init
        case th.THIRPtrLocalDecl() if stmt.kind is th.PtrSlotKind.PTR_ADDR:
            value = stmt.init
        case th.THIRPtrLocalRebind() if stmt.kind is th.PtrSlotKind.PTR_ADDR:
            value = stmt.value
        case _:
            return None
    return None if value is None or _stable(value) else value


class _Collector:
    def __init__(self, plan: THIRTempPlan | None,
                 borrowed_result: th.THIRBorrowedRecord | None) -> None:
        self.plan = plan
        self.borrowed_result = borrowed_result
        self.backings: list[THIRBacking] = []
        self.sinks: list[tuple[th.THIRStmt, th.THIRExpr]] = []

    def holder(self, holder: object, node: object, nested: bool) -> None:
        if isinstance(node, th.THIRStmt):
            value = _borrowed_value(node, None if nested else self.borrowed_result)
            if value is not None:
                self.sinks.append((node, value))
        nested = nested or isinstance(node, _NESTED_BODIES)
        for child in _children(node):
            if isinstance(child, th.THIRStmt):
                self.holder(child, child, nested)
            elif isinstance(child, th.THIRExpr):
                self.expr(child, holder, child, nested)
            else:
                self.holder(holder, child, nested)

    def expr(self, node: object, holder: object, root: th.THIRExpr, nested: bool) -> None:
        if isinstance(node, th.THIRExpr):
            self.produce(node, holder, root, nested)
        nested = nested or isinstance(node, _NESTED_BODIES)
        for child in _children(node):
            if isinstance(child, th.THIRStmt):
                self.holder(child, child, nested)
            else:
                self.expr(child, holder, root, nested)

    def produce(self, node: th.THIRExpr, holder: object, root: th.THIRExpr, nested: bool) -> None:
        placement = None
        match node:
            case th.THIRArgTemp():
                kind = THIRBackingKind.ARGUMENT
                placement = self.plan.by_node.get(node) if self.plan is not None else None
                uncovered = (None if placement is not None
                             else "argument storage has no temporary plan" if self.plan is None
                             else "argument storage outside the temporary plan")
            case th.THIRSlotEmplace():
                kind = THIRBackingKind.SELECT_SLOT
                uncovered = "select slot placement is not planned"
            case th.THIRCtorCall() if node.full_expression_storage is not None:
                kind = THIRBackingKind.FULL_EXPRESSION
                uncovered = None
            case _:
                return
        if nested:
            placement, uncovered = None, "storage inside a nested body"
        loc = node.loc if node.loc is not None else getattr(holder, "loc", None)
        self.backings.append(THIRBacking(len(self.backings), kind, node, holder, root,
                                         placement, loc, uncovered))

    def build(self, body: tuple[th.THIRStmt, ...], initializers: tuple[object, ...]) -> THIRStorageFacts:
        for cell in initializers:
            self.holder(cell, cell, False)
        for stmt in body:
            self.holder(stmt, stmt, False)
        obligations = tuple(
            THIRBorrowObligation(index, sink, value,
                                 tuple(b.index for b in self.backings if b.full_expression is value))
            for index, (sink, value) in enumerate(self.sinks))
        return THIRStorageFacts(body, initializers, self.borrowed_result, tuple(self.backings), obligations,
                                MappingProxyType(IdentityMap((b.node, b) for b in self.backings)))


def collect_storage_facts(body: tuple[th.THIRStmt, ...], plan: THIRTempPlan | None,
                          initializers: tuple[object, ...] = (), *,
                          borrowed_result: th.THIRBorrowedRecord | None = None) -> THIRStorageFacts:
    """`initializers` are a constructor's member and base initializer cells."""
    return _Collector(plan, borrowed_result).build(body, initializers)


def validate_storage_facts(body: tuple[th.THIRStmt, ...], plan: THIRTempPlan | None,
                           facts: THIRStorageFacts, initializers: tuple[object, ...] = (), *,
                           borrowed_result: th.THIRBorrowedRecord | None = None) -> None:
    expected = collect_storage_facts(body, plan, initializers, borrowed_result=borrowed_result)
    if (facts.body is not body or len(facts.initializers) != len(initializers)
            or facts.borrowed_result is not borrowed_result
            or any(a is not b for a, b in zip(facts.initializers, initializers))
            or len(facts.backings) != len(expected.backings)
            or len(facts.obligations) != len(expected.obligations)):
        raise ValueError("invalid or stale storage facts")
    for actual, wanted in zip(facts.backings, expected.backings):
        if (actual.index != wanted.index or actual.kind is not wanted.kind
                or actual.node is not wanted.node or actual.holder is not wanted.holder
                or actual.full_expression is not wanted.full_expression
                or actual.placement is not wanted.placement
                or actual.loc != wanted.loc or actual.uncovered != wanted.uncovered):
            raise ValueError("invalid or stale storage facts")
    for actual, wanted in zip(facts.obligations, expected.obligations):
        if (actual.index != wanted.index or actual.sink is not wanted.sink
                or actual.value is not wanted.value or actual.backings != wanted.backings):
            raise ValueError("invalid or stale storage facts")
    if (len(facts.by_node) != len(facts.backings)
            or any(facts.by_node.get(entry.node) is not entry for entry in facts.backings)):
        raise ValueError("invalid or stale storage facts")
