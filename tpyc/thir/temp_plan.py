"""Semantic declaration and initialization anchors for named argument storage."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..codegen_cpp.forms import LocalBinding
from ..identity_map import IdentityMap, IdentitySet
from ..temp_schedule import TempQueue, banks_in_region
from . import nodes as th
from .metadata import unsupported_metadata


def is_elif(outer: th.THIRIf, inner: th.THIRIf) -> bool:
    """Elif keeps the outer column; unlocated macro fragments use chain form."""
    if outer.loc is None and inner.loc is None:
        return True
    return outer.loc is not None and inner.loc is not None and outer.loc.column == inner.loc.column


def if_chain(stmt: th.THIRIf) -> tuple[th.THIRIf, ...]:
    chain = [stmt]
    while (len(chain[-1].else_body) == 1 and isinstance(chain[-1].else_body[0], th.THIRIf)
           and not chain[-1].else_is_nested and is_elif(chain[-1], chain[-1].else_body[0])):
        chain.append(chain[-1].else_body[0])
    return tuple(chain)


@dataclass(frozen=True)
class THIRTempScope:
    index: int
    parent: int | None
    owner: object
    role: str


@dataclass(frozen=True)
class THIRTempPlacement:
    index: int
    node: th.THIRArgTemp
    scope: int
    declaration: th.THIRStmt
    initialization: th.THIRNode
    optional: bool


@dataclass(frozen=True)
class THIRTempPlan:
    body: tuple[th.THIRStmt, ...]
    scopes: tuple[THIRTempScope, ...]
    placements: tuple[THIRTempPlacement, ...]
    by_node: Mapping[th.THIRArgTemp, THIRTempPlacement]
    declarations: Mapping[th.THIRStmt, tuple[THIRTempPlacement, ...]]
    initializations: Mapping[th.THIRNode, tuple[THIRTempPlacement, ...]]
    scope_ids: Mapping[object, Mapping[str, int]]

    def placement(self, node: th.THIRArgTemp) -> THIRTempPlacement:
        entry = self.by_node.get(node)
        if entry is None or entry.node is not node:
            raise ValueError("foreign argument temporary")
        return entry

    def scope(self, owner: object, role: str) -> int | None:
        return self.scope_ids.get(owner, {}).get(role)


class _Unplanned(Exception):
    pass


def _plain(node: object, allowed: set[str]) -> None:
    if unsupported_metadata(node, allowed) is not None:
        raise _Unplanned()


class _Planner:
    def __init__(self, body: tuple[th.THIRStmt, ...]) -> None:
        self.body = body
        self.queue: TempQueue[th.THIRArgTemp] = TempQueue()
        self.scopes = [THIRTempScope(0, None, body, "body")]
        self.placements: list[THIRTempPlacement] = []
        self.initializers: IdentityMap[th.THIRArgTemp, th.THIRExpr] = IdentityMap()
        self.seen: IdentitySet[th.THIRArgTemp] = IdentitySet()

    def scope(self, owner: object, role: str, parent: int) -> int:
        index = len(self.scopes)
        self.scopes.append(THIRTempScope(index, parent, owner, role))
        return index

    def lazy(self, expr: th.THIRExpr) -> None:
        region = self.queue.begin()
        self.expr(expr)
        for entry in self.queue.end(region):
            self.initializers[entry.value] = expr

    def expr(self, expr: th.THIRExpr) -> None:
        match expr:
            case th.THIRArgTemp():
                if expr in self.seen:
                    raise _Unplanned()
                self.seen.add(expr)
                self.expr(expr.init)
                self.queue.register(expr, banks_in_region(expr.cpp_type or "auto", expr.movable))
            case th.THIRName() | th.THIRSelf() | th.THIRLiteral():
                pass
            case th.THIRFieldAccess():
                _plain(expr, {"receiver", "field_cpp", "field_identity", "is_arrow", "full_expression_storage"})
                self.expr(expr.receiver)
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name"})
                self.expr(expr.expr)
            case th.THIRUnaryNot():
                self.expr(expr.operand)
            case th.THIRCall():
                _plain(expr, {"callee", "args", "callee_cpp", "resolved_callee"})
                for arg in expr.args:
                    self.expr(arg)
            case th.THIRCtorCall():
                _plain(expr, {"type_cpp", "args", "full_expression_storage"})
                for arg in expr.args:
                    self.expr(arg)
            case th.THIRBinOp():
                self.expr(expr.left)
                if expr.resolved is None and expr.op in ("&&", "||"):
                    self.lazy(expr.right)
                else:
                    self.expr(expr.right)
            case th.THIRValueSelect():
                _plain(expr, {"lhs", "rhs", "op", "truthy_mode", "lhs_cast", "rhs_cast", "rhs_sv"})
                self.expr(expr.lhs)
                self.lazy(expr.rhs)
            case th.THIRIfExpr():
                self.expr(expr.cond)
                self.lazy(expr.then)
                self.lazy(expr.orelse)
            case _:
                raise _Unplanned()

    def flush(self, stmt: th.THIRStmt, scope: int) -> None:
        for entry in self.queue.drain():
            node = entry.value
            self.placements.append(THIRTempPlacement(
                len(self.placements), node, scope, stmt,
                self.initializers[node] if entry.deferred else stmt, entry.optional))

    def stmts(self, body: tuple[th.THIRStmt, ...], scope: int) -> None:
        for stmt in body:
            match stmt:
                case th.THIRIf():
                    current = scope
                    chain = if_chain(stmt)
                    for index, arm in enumerate(chain):
                        self.expr(arm.condition)
                        if index and self.queue.pending:
                            current = self.scope(arm, "condition", current)
                        self.flush(arm, current)
                        self.stmts(arm.then_body, self.scope(arm, "then", current))
                    if chain[-1].else_body:
                        self.stmts(chain[-1].else_body, self.scope(chain[-1], "else", current))
                case th.THIRWhile():
                    self.expr(stmt.condition)
                    repeated = self.scope(stmt, "iteration", scope) if self.queue.pending else None
                    self.flush(stmt, scope if repeated is None else repeated)
                    body_scope = repeated if repeated is not None else self.scope(stmt, "loop", scope)
                    self.stmts(stmt.body, body_scope)
                    if stmt.orelse:
                        self.stmts(stmt.orelse, self.scope(stmt, "else", scope))
                case th.THIRVarDecl():
                    if stmt.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS:
                        raise _Unplanned()
                    _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "form", "is_const",
                                  "cpp_local_representation", "owned_storage", "storage_placement", "storage_borrow"})
                    if stmt.init is not None:
                        self.expr(stmt.init)
                    self.flush(stmt, scope)
                case th.THIRAssign():
                    _plain(stmt, {"target", "value"})
                    self.expr(stmt.target)
                    self.expr(stmt.value)
                    self.flush(stmt, scope)
                case th.THIRReturn():
                    _plain(stmt, {"value"})
                    if stmt.value is not None:
                        self.expr(stmt.value)
                    self.flush(stmt, scope)
                case th.THIRExprStmt():
                    _plain(stmt, {"expr"})
                    self.expr(stmt.expr)
                    self.flush(stmt, scope)
                case th.THIRBreak() | th.THIRContinue() | th.THIRNoOpStmt():
                    _plain(stmt, set())
                case _:
                    raise _Unplanned()

    def build(self) -> THIRTempPlan:
        self.stmts(self.body, 0)
        declarations: IdentityMap[th.THIRStmt, list[THIRTempPlacement]] = IdentityMap()
        initializations: IdentityMap[th.THIRNode, list[THIRTempPlacement]] = IdentityMap()
        scopes: IdentityMap[object, dict[str, int]] = IdentityMap()
        for scope in self.scopes:
            scopes.setdefault(scope.owner, {})[scope.role] = scope.index
        for entry in self.placements:
            declarations.setdefault(entry.declaration, []).append(entry)
            initializations.setdefault(entry.initialization, []).append(entry)
        return THIRTempPlan(self.body, tuple(self.scopes), tuple(self.placements),
                            MappingProxyType(IdentityMap((e.node, e) for e in self.placements)),
                            MappingProxyType(IdentityMap((k, tuple(v)) for k, v in declarations.items())),
                            MappingProxyType(IdentityMap((k, tuple(v)) for k, v in initializations.items())),
                            MappingProxyType(IdentityMap((k, MappingProxyType(v)) for k, v in scopes.items())))


def prepare_temporaries(body: tuple[th.THIRStmt, ...]) -> THIRTempPlan | None:
    try:
        plan = _Planner(body).build()
        return plan if plan.placements else None
    except _Unplanned:
        return None


def validate_plan(body: tuple[th.THIRStmt, ...], plan: THIRTempPlan) -> None:
    expected = prepare_temporaries(body)
    if (plan.body is not body or expected is None
            or len(plan.placements) != len(expected.placements)
            or len(plan.scopes) != len(expected.scopes)):
        raise ValueError("invalid or stale temporary placement plan")
    for actual, wanted in zip(plan.placements, expected.placements):
        if (actual.index != wanted.index or actual.node is not wanted.node
                or actual.scope != wanted.scope or actual.declaration is not wanted.declaration
                or actual.initialization is not wanted.initialization or actual.optional != wanted.optional):
            raise ValueError("invalid or stale temporary placement plan")
    for actual, wanted in zip(plan.scopes, expected.scopes):
        if (actual.index != wanted.index or actual.parent != wanted.parent
                or actual.owner is not wanted.owner or actual.role != wanted.role):
            raise ValueError("invalid or stale temporary placement plan")
    if (len(plan.by_node) != len(plan.placements)
            or any(plan.by_node.get(entry.node) is not entry for entry in plan.placements)
            or len(plan.scope_ids) != len(expected.scope_ids)
            or any(plan.scope_ids.get(owner) != roles for owner, roles in expected.scope_ids.items())):
        raise ValueError("invalid or stale temporary placement plan")
    for actual, wanted in ((plan.declarations, expected.declarations),
                           (plan.initializations, expected.initializations)):
        if (len(actual) != len(wanted)
                or any(key not in actual or len(actual[key]) != len(entries)
                       or any(a is not plan.placements[w.index] for a, w in zip(actual[key], entries))
                       for key, entries in wanted.items())):
            raise ValueError("invalid or stale temporary placement plan")
