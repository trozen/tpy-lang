"""The syntactic climb every storage question projects (`trace`).

`trace` climbs an expression once, over coercions, field / subscript steps,
property getters, walruses and borrow-returning awaits, and ends at a name, a
select, a `*args` pack, a call or any other node. It reads nodes only: the
projections over it (`tpyc/sema/storage_walk.py`) read the type facts and the
call policies they need. It sits below sema so a pre-sema pass
(`tpyc/prescan.py`) reads the same climb sema does; the path roots here are
its projections that need no fact at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .parse import (
    TpyAwait, TpyBinOp, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess,
    TpyGeneratorExpression, TpyIfExpr, TpyMethodCall, TpyName, TpyNamedExpr,
    TpyStarUnpack, TpySubscript, TpyUnaryOp, TpyVarargPack,
    is_property_getter_read,
)


class StepKind(Enum):
    """One syntactic step of a `Trace`, from a node to the operand it reads."""
    COERCE = "coerce"  # a coercion, onto its operand
    FIELD = "field"    # a field access (`__getattr__` and unbound `Base.f` too)
    GETTER = "getter"  # a property getter read, onto its receiver
    INDEX = "index"    # a subscript, onto the subscripted object
    WALRUS = "walrus"  # a walrus, onto the value it binds
    AWAIT = "await"    # an await whose result is a borrow, onto the awaited call
    # A call a projection climbed through, onto an operand it lends: a step of
    # a projection's path (`Leaf.path`), never of a `Trace`.
    CALL = "call"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    node: TpyExpr
    # A CALL step only: how the call lent the operand the path goes on into
    # (the policy's `Lent` answer).
    how: Enum | None = field(default=None, compare=False)


def is_call_end(expr: TpyExpr) -> bool:
    """Whether a trace ending at `expr` ends at a call: a function or method
    call (a property getter read is a step instead), a resolved operator
    dunder, or a generator expression (the call building its frame). Which
    operands its result may point into is a call policy's answer."""
    if isinstance(expr, (TpyCall, TpyGeneratorExpression)):
        return True
    if isinstance(expr, TpyMethodCall):
        return not is_property_getter_read(expr)
    if isinstance(expr, TpyBinOp):
        return expr.resolved_binop is not None
    return isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None


@dataclass(frozen=True)
class Trace:
    """The syntactic climb of an expression: its `steps` from the top node
    down, and the node the climb ended at -- a name, a select or a `*args`
    pack (`arms`, one trace per operand it stands for), a call
    (`is_call_end`: a branch per operand, traced on demand), or any other
    node no step reads through. It records nodes only; each projection
    reads the type facts it needs and asks its call policy what a call
    lends, and the node it cannot step past is the one it reports."""
    steps: tuple[Step, ...]
    end: TpyExpr
    arms: tuple['Trace', ...] = ()

    def node_at(self, i: int) -> TpyExpr:
        return self.steps[i].node if i < len(self.steps) else self.end

    def skip(self, i: int = 0,
             kinds: tuple[StepKind, ...] = (StepKind.COERCE,)) -> int:
        """The first position at or past `i` whose step is none of `kinds`
        (by default: past the coercions)."""
        while i < len(self.steps) and self.steps[i].kind in kinds:
            i += 1
        return i

    @property
    def is_pack(self) -> bool:
        return isinstance(self.end, TpyVarargPack)

    @property
    def ends_at_call(self) -> bool:
        return not self.arms and is_call_end(self.end)

    def leaves(self, i: int = 0, *, ternary_only: bool = False,
               past_walrus: bool = False) -> list[tuple['Trace', int]]:
        """The operands the value at `i` may BE, as (trace, position): the
        value past its coercions -- and with `past_walrus` its walruses, the
        value one binds -- or for a select end every operand the select
        picks, nested selects flattened (with `ternary_only`, a ternary only
        -- an and/or select is then an operand itself). A `*args` pack is no
        value a binding or a receiver can be: it is its own single operand
        here."""
        j = self.skip(i, (StepKind.COERCE, StepKind.WALRUS) if past_walrus
                      else (StepKind.COERCE,))
        if j == len(self.steps) and self.arms and not self.is_pack and (
                not ternary_only or isinstance(self.end, TpyIfExpr)):
            return [leaf for arm in self.arms
                    for leaf in arm.leaves(ternary_only=ternary_only,
                                           past_walrus=past_walrus)]
        return [(self, j)]


def trace(expr: TpyExpr) -> Trace:
    """The one syntactic climb every storage question projects (module
    docstring)."""
    steps: list[Step] = []
    while True:
        if isinstance(expr, TpyCoerce):
            steps.append(Step(StepKind.COERCE, expr))
            expr = expr.expr
        elif isinstance(expr, TpyFieldAccess):
            steps.append(Step(StepKind.FIELD, expr))
            expr = expr.obj
        elif isinstance(expr, TpySubscript):
            steps.append(Step(StepKind.INDEX, expr))
            expr = expr.obj
        elif is_property_getter_read(expr):
            steps.append(Step(StepKind.GETTER, expr))
            expr = expr.obj
        elif isinstance(expr, TpyNamedExpr):
            steps.append(Step(StepKind.WALRUS, expr))
            expr = expr.value
        elif isinstance(expr, TpyAwait) and expr.await_result_is_borrow:
            steps.append(Step(StepKind.AWAIT, expr))
            expr = expr.value
        else:
            break
    arms: tuple[Trace, ...] = ()
    if isinstance(expr, TpyIfExpr):
        arms = (trace(expr.then_expr), trace(expr.else_expr))
    elif isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        arms = (trace(expr.left), trace(expr.right))
    elif isinstance(expr, TpyVarargPack):
        # A callee that borrows the pack borrows every element; a
        # forwarded pack (`f(*xs)`) stands for the source pack itself.
        arms = tuple(trace(a.expr if isinstance(a, TpyStarUnpack) else a)
                     for a in expr.args)
    return Trace(tuple(steps), expr, arms)


def is_unbound_field(step: Step) -> bool:
    """An unbound `Base.f` hop: it names this object's field, `self`'s."""
    return (step.kind is StepKind.FIELD
            and step.node.unbound_self_parent_type is not None)


FIELD_PATH = (StepKind.FIELD,)
MEMBER_PATH = (StepKind.FIELD, StepKind.INDEX, StepKind.GETTER)


def path_root(expr: TpyExpr,
              kinds: tuple[StepKind, ...] = FIELD_PATH) -> str | None:
    """The name a pure path of `kinds` steps off a name roots at (`o.a.b`
    -> 'o'), or None when the climb takes any other step or ends at
    anything but a name. An unbound `Base.f` roots at `self`."""
    t = trace(expr)
    if t.arms or not isinstance(t.end, TpyName):
        return None
    root = t.end.name
    for step in t.steps:
        if step.kind not in kinds:
            return None
        if is_unbound_field(step):
            root = "self"
    return root
