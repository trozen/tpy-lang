"""All-or-nothing lowering of the approved scalar THIR subset."""

from dataclasses import MISSING, dataclass, field, fields

from ..identity_map import IdentityMap
from ..parse import SourceLocation
from ..thir import nodes as th
from ..typesys import BOOL, INT32, INT32_MAX, INT32_MIN, IntLiteralType, TpyType, VoidType
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBranch,
    MIRCompare, MIRConstant, MIRGoto, MIRFunction, MIRNot, MIRNotCovered,
    MIRRead, MIRReturn, MIRRvalue, MIRSlot, MIRSlotId, MIRSlotKind, MIRTerminator,
)
from .validate import successors, validate_function


class _Unsupported(Exception):
    def __init__(self, node: object, reason: str) -> None:
        self.node = node
        self.reason = reason


def _require(node: object, condition: bool, reason: str) -> None:
    if not condition:
        raise _Unsupported(node, reason)


def _plain(node: th.THIRNode, allowed: set[str]) -> None:
    # New non-default metadata must not silently acquire scalar semantics.
    for f in fields(node):
        if f.name in allowed | {"loc", "result_type", "form"}:
            continue
        default = f.default
        if default is MISSING and f.default_factory is not MISSING:
            default = f.default_factory()
        _require(node, default is not MISSING and getattr(node, f.name) == default,
                 f"unsupported metadata: {f.name}")


def _literal(expr: th.THIRExpr) -> bool:
    return isinstance(expr, th.THIRLiteral) or (
        isinstance(expr, th.THIRCoerce) and isinstance(expr.expr, th.THIRLiteral))


class _Coverage:
    def __init__(self, fn: th.THIRFunction) -> None:
        self.fn = fn
        self.bindings: dict[str, TpyType] = {}
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()

    def check(self) -> None:
        fn = self.fn
        _require(fn, fn.error_return_cpp is None, "error-return body")
        _require(fn, not fn.layout.hoisted_locals, "hoisted declarations")
        _require(fn, fn.return_type in (BOOL, INT32) or isinstance(fn.return_type, VoidType),
                 "unsupported return type")
        for p in fn.params:
            _require(fn, p.type in (BOOL, INT32), "unsupported parameter type")
            _require(fn, p.name not in self.bindings, "duplicate binding")
            self.bindings[p.name] = p.type
        prefix = True
        for stmt in fn.body:
            if isinstance(stmt, th.THIRVarDecl):
                _require(stmt, prefix, "declaration outside entry prefix")
                _plain(stmt, {"name", "resolved_type", "init", "is_const"})
                _require(stmt, stmt.form is th.Form.VALUE and stmt.resolved_type in (BOOL, INT32),
                         "unsupported local type or form")
                _require(stmt, stmt.name not in self.bindings, "duplicate binding")
                if stmt.init is not None:
                    _require(stmt, self.expr(stmt.init) == stmt.resolved_type,
                             "initializer type mismatch")
                self.bindings[stmt.name] = stmt.resolved_type
            else:
                if not isinstance(stmt, th.THIRNoOpStmt):
                    prefix = False
                self.stmt(stmt, 0)

    def expr(self, expr: th.THIRExpr) -> TpyType:
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        typ = expr.result_type
        if isinstance(expr, th.THIRLiteral) and isinstance(typ, IntLiteralType):
            typ = INT32
        _require(expr, typ in (BOOL, INT32), "unsupported expression type")
        writing = False
        if isinstance(expr, th.THIRLiteral):
            _plain(expr, {"value", "int_cpp"})
            _require(expr, (typ == BOOL and type(expr.value) is bool)
                     or (typ == INT32 and type(expr.value) is int
                         and INT32_MIN <= expr.value <= INT32_MAX),
                     "unsupported literal value")
        elif isinstance(expr, th.THIRName):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, expr.name in self.bindings, "non-local name")
            _require(expr, self.bindings[expr.name] == typ, "name type mismatch")
        elif isinstance(expr, th.THIRCoerce):
            _plain(expr, {"expr", "coercion_name"})
            _require(expr, typ == INT32
                     and expr.coercion_name == "int_literal_to_fixed_int"
                     and isinstance(expr.expr, th.THIRLiteral), "unsupported coercion")
            _require(expr, self.expr(expr.expr) == INT32, "unsupported literal coercion")
        elif isinstance(expr, th.THIRWalrus):
            _plain(expr, {"name", "cpp_name", "value"})
            _require(expr, self.bindings.get(expr.name) == typ, "walrus needs existing scalar local")
            _require(expr, self.expr(expr.value) == typ, "walrus type mismatch")
            writing = True
        elif isinstance(expr, th.THIRUnaryNot):
            _plain(expr, {"operand"})
            _require(expr, typ == BOOL and self.expr(expr.operand) == BOOL,
                     "not requires bool")
            writing = self.writes[expr.operand]
        elif isinstance(expr, th.THIRBinOp):
            _plain(expr, {"left", "right", "op", "resolved", "paren_wrap"})
            _require(expr, expr.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="),
                     "unsupported binary operation")
            left, right = self.expr(expr.left), self.expr(expr.right)
            _require(expr, typ == BOOL and left == right, "comparison operand type mismatch")
            if expr.op in ("&&", "||"):
                _require(expr, left == BOOL and expr.resolved is None, "unsupported boolean operation")
            else:
                if ((self.writes[expr.left] and not _literal(expr.right))
                        or (self.writes[expr.right] and not _literal(expr.left))):
                    raise _Unsupported(expr, "order-sensitive eager operands")
                rb = expr.resolved
                if rb is not None:
                    expected = {"<": "__lt__", "<=": "__le__", ">": "__gt__",
                                ">=": "__ge__", "==": "__eq__", "!=": "__ne__"}
                    _require(expr, rb.receiver_type in (BOOL, INT32)
                             and rb.method.name in (expected[expr.op],
                                                    "__eq__" if expr.op == "!=" else expected[expr.op])
                             and not rb.is_reverse
                             and rb.left_wrapper == "{expr}" and rb.right_wrapper == "{expr}",
                             "unsupported comparison dispatch")
            writing = self.writes[expr.left] or self.writes[expr.right]
        elif isinstance(expr, th.THIRValueSelect):
            _plain(expr, {"lhs", "rhs", "op", "lhs_temp_cpp"})
            _require(expr, expr.op in ("&&", "||") and typ == BOOL
                     and self.expr(expr.lhs) == BOOL and self.expr(expr.rhs) == BOOL,
                     "unsupported value select")
            # A pure bool select never needs a representation-changing temp.
            _require(expr, expr.lhs_temp_cpp in (None, "auto&&"), "unsupported select temporary")
            writing = self.writes[expr.lhs] or self.writes[expr.rhs]
        elif isinstance(expr, th.THIRIfExpr):
            _plain(expr, {"cond", "then", "orelse"})
            _require(expr, self.expr(expr.cond) == BOOL, "condition requires bool")
            _require(expr, self.expr(expr.then) == typ and self.expr(expr.orelse) == typ,
                     "conditional arm type mismatch")
            writing = any(self.writes[e] for e in (expr.cond, expr.then, expr.orelse))
        else:
            raise _Unsupported(expr, "unsupported expression")
        self.writes[expr] = writing
        return typ

    def stmt(self, stmt: th.THIRStmt, loops: int) -> None:
        if isinstance(stmt, th.THIRNoOpStmt):
            _plain(stmt, set())
        elif isinstance(stmt, th.THIRAssign):
            _plain(stmt, {"target", "value"})
            _require(stmt, isinstance(stmt.target, th.THIRName), "assignment needs local target")
            _require(stmt, self.expr(stmt.target) == self.expr(stmt.value), "assignment type mismatch")
        elif isinstance(stmt, th.THIRExprStmt):
            _plain(stmt, {"expr", "void_cast"})
            self.expr(stmt.expr)
        elif isinstance(stmt, th.THIRReturn):
            _plain(stmt, {"value"})
            if stmt.value is None:
                _require(stmt, isinstance(self.fn.return_type, VoidType), "missing return value")
            else:
                _require(stmt, self.expr(stmt.value) == self.fn.return_type, "return type mismatch")
        elif isinstance(stmt, (th.THIRIf, th.THIRWhile)):
            allowed = {"condition", "then_body", "else_body", "else_is_nested"} if isinstance(
                stmt, th.THIRIf) else {"condition", "body", "orelse"}
            _plain(stmt, allowed)
            _require(stmt, self.expr(stmt.condition) == BOOL, "condition requires bool")
            if isinstance(stmt, th.THIRIf):
                for child in stmt.then_body + stmt.else_body:
                    self.stmt(child, loops)
            else:
                for child in stmt.body:
                    self.stmt(child, loops + 1)
                for child in stmt.orelse:
                    self.stmt(child, loops)
        elif isinstance(stmt, (th.THIRBreak, th.THIRContinue)):
            _plain(stmt, set())
            _require(stmt, loops > 0, "loop control outside loop")
        else:
            raise _Unsupported(stmt, "unsupported statement")


@dataclass
class _Block:
    id: MIRBlockId
    statements: list[MIRAssign] = field(default_factory=list)
    terminator: MIRTerminator | None = None


class _Builder:
    def __init__(self, body: MIRBodyId, fn: th.THIRFunction) -> None:
        self.body = body
        self.fn = fn
        self.slots: list[MIRSlot] = []
        self.bindings: dict[str, MIRSlotId] = {}
        self.blocks: list[_Block] = []
        self.current: _Block | None = self.block()
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []

    def block(self) -> _Block:
        block = _Block(MIRBlockId(self.body, len(self.blocks)))
        self.blocks.append(block)
        return block

    def slot(self, typ: TpyType, kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
             name: str | None = None) -> MIRSlotId:
        sid = MIRSlotId(self.body, len(self.slots))
        self.slots.append(MIRSlot(sid, typ, kind, name))
        return sid

    def write(self, dest: MIRSlotId, value: MIRRvalue, loc: SourceLocation | None) -> None:
        assert self.current is not None
        self.current.statements.append(MIRAssign(dest, value, loc))

    def end(self, term: MIRTerminator) -> None:
        assert self.current is not None and self.current.terminator is None
        self.current.terminator = term
        self.current = None

    def result(self, typ: TpyType, value: MIRRvalue, loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        self.write(dest, value, loc)
        return dest

    def select(self, cond: MIRSlotId, then: th.THIRExpr | MIRSlotId,
               otherwise: th.THIRExpr | MIRSlotId, typ: TpyType,
               loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        yes, no, join = self.block(), self.block(), self.block()
        self.end(MIRBranch(cond, yes.id, no.id, loc))
        for block, arm in ((yes, then), (no, otherwise)):
            self.current = block
            value = arm if isinstance(arm, MIRSlotId) else self.expr(arm)
            self.write(dest, MIRRead(value), loc)
            self.end(MIRGoto(join.id, loc))
        self.current = join
        return dest

    def expr(self, expr: th.THIRExpr) -> MIRSlotId:
        typ = INT32 if isinstance(expr.result_type, IntLiteralType) else expr.result_type
        loc = expr.loc
        if isinstance(expr, th.THIRLiteral):
            return self.result(typ, MIRConstant(expr.value), loc)
        if isinstance(expr, th.THIRCoerce):
            return self.result(INT32, MIRConstant(expr.expr.value), loc)
        if isinstance(expr, th.THIRName):
            return self.result(typ, MIRRead(self.bindings[expr.name]), loc)
        if isinstance(expr, th.THIRWalrus):
            value = self.expr(expr.value)
            self.write(self.bindings[expr.name], MIRRead(value), loc)
            return value
        if isinstance(expr, th.THIRUnaryNot):
            return self.result(BOOL, MIRNot(self.expr(expr.operand)), loc)
        if isinstance(expr, th.THIRBinOp):
            left = self.expr(expr.left)
            if expr.op in ("&&", "||"):
                return self.select(left, expr.right if expr.op == "&&" else left,
                                   left if expr.op == "&&" else expr.right, BOOL, loc)
            right = self.expr(expr.right)
            return self.result(BOOL, MIRCompare(expr.op, left, right), loc)
        if isinstance(expr, th.THIRValueSelect):
            left = self.expr(expr.lhs)
            return self.select(left, expr.rhs if expr.op == "&&" else left,
                               left if expr.op == "&&" else expr.rhs, BOOL, loc)
        if isinstance(expr, th.THIRIfExpr):
            return self.select(self.expr(expr.cond), expr.then, expr.orelse, typ, loc)
        raise AssertionError("coverage and expression lowering disagree")

    def stmts(self, stmts: tuple[th.THIRStmt, ...]) -> None:
        for stmt in stmts:
            if self.current is None:
                # Coverage already inspected every retained unreachable node.
                break
            loc = stmt.loc
            if isinstance(stmt, th.THIRNoOpStmt):
                continue
            if isinstance(stmt, th.THIRVarDecl):
                dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name)
                if stmt.init is not None:
                    self.write(dest, MIRRead(self.expr(stmt.init)), loc)
                self.bindings[stmt.name] = dest
            elif isinstance(stmt, th.THIRAssign):
                self.write(self.bindings[stmt.target.name], MIRRead(self.expr(stmt.value)), loc)
            elif isinstance(stmt, th.THIRExprStmt):
                self.expr(stmt.expr)
            elif isinstance(stmt, th.THIRReturn):
                self.end(MIRReturn(self.expr(stmt.value) if stmt.value is not None else None, loc))
            elif isinstance(stmt, th.THIRIf):
                cond = self.expr(stmt.condition)
                yes, no = self.block(), self.block()
                self.end(MIRBranch(cond, yes.id, no.id, loc))
                exits = []
                for block, arm in ((yes, stmt.then_body), (no, stmt.else_body)):
                    self.current = block
                    self.stmts(arm)
                    if self.current is not None:
                        exits.append(self.current)
                if exits:
                    join = self.block()
                    for block in exits:
                        self.current = block
                        self.end(MIRGoto(join.id, loc))
                    self.current = join
                else:
                    self.current = None
            elif isinstance(stmt, th.THIRWhile):
                cond_block, body, normal, after = self.block(), self.block(), self.block(), self.block()
                self.end(MIRGoto(cond_block.id, loc))
                self.current = cond_block
                self.end(MIRBranch(self.expr(stmt.condition), body.id, normal.id, loc))
                self.current = body
                self.loops.append((cond_block.id, after.id))
                self.stmts(stmt.body)
                self.loops.pop()
                if self.current is not None:
                    self.end(MIRGoto(cond_block.id, loc))
                self.current = normal
                self.stmts(stmt.orelse)
                if self.current is not None:
                    self.end(MIRGoto(after.id, loc))
                self.current = after
            elif isinstance(stmt, th.THIRBreak):
                self.end(MIRGoto(self.loops[-1][1], loc))
            elif isinstance(stmt, th.THIRContinue):
                self.end(MIRGoto(self.loops[-1][0], loc))
            else:
                raise AssertionError("coverage and statement lowering disagree")

    def build(self) -> MIRFunction:
        for p in self.fn.params:
            self.bindings[p.name] = self.slot(p.type, MIRSlotKind.PARAMETER, p.name)
        self.stmts(self.fn.body)
        reachable: set[MIRBlockId] = set()
        pending = [self.blocks[0].id]
        while pending:
            bid = pending.pop()
            if bid in reachable:
                continue
            reachable.add(bid)
            term = self.blocks[bid.index].terminator
            if term is not None:
                pending.extend(successors(term))
        if self.current is not None and self.current.id in reachable:
            _require(self.fn, isinstance(self.fn.return_type, VoidType), "non-void fallthrough")
            self.end(MIRReturn())
        blocks = []
        for b in self.blocks:
            if b.id not in reachable:
                continue
            assert b.terminator is not None
            blocks.append(MIRBlock(b.id, tuple(b.statements), b.terminator))
        fn = MIRFunction(self.body, self.fn.return_type, tuple(self.slots), tuple(blocks),
                         self.blocks[0].id)
        validate_function(fn)
        return fn


def lower_function(fn: th.THIRFunction, body: MIRBodyId, *,
                   kind: MIRBodyKind) -> MIRFunction | MIRNotCovered:
    """The caller supplies declaration kind; THIRFunction alone loses it."""
    try:
        _require(fn, kind is MIRBodyKind.FREE_FUNCTION, "unsupported body kind")
        _Coverage(fn).check()
        return _Builder(body, fn).build()
    except _Unsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))
