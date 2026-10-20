"""The parts of an augmented assignment's target it evaluates once.

`t[k] op= v` reads `t[k]` and stores into it, as does `o.n op= v` with
`o.n`. A target part whose evaluation is observable when repeated -- a
call into a body, a check point, a writing builtin, a walrus -- runs once,
as under CPython: the statement evaluates it ahead of the store, and the
read and the store both read it by name (`TpyAugAssign.hold_parts`).
Everything else is spelled as the explicit dunder call spells it.

Three boundaries keep what is held sound without a copy:

* REGIONS. A ternary, `and` / `or`, a chained comparison, a comprehension,
  a generator expression or a lambda runs its parts conditionally,
  repeatedly or later, so one holding an observable node is held whole,
  at the outermost such region; nothing inside it is held apart. Its
  value must be a number or a value it creates on every arm
  (`label() if f else names[0]` would copy `names[0]`).
* CONSUMPTION. A part read by a slot that takes it by value -- a
  parameter or receiver that takes ownership, a container literal's
  element -- has its reader held too, so the one read passes it as the
  reader's own analysis decided: a value the part creates moves in inline
  (so it is not held apart). A slot that borrows reads the held part in
  place.
* BACKING. A held view or reference is held after the temporaries it may
  point into (`borrow_temp_roots`, every one: each operand when the
  callee's lend facts are unknown), which are held too. What else it
  points into is a held part or storage outside the statement.

A held value that is no scalar, view or region must be positively known
to be new (`result_is_fresh`), or is held through the nearest enclosing
part that is a number or a new value (`_HoldPlan.lift`); a receiver is
held as a place. A target
these cannot hold -- a value not known to be new, a region whose value is
a view, a reference or a choice between a new value and existing storage,
a temporary behind a view chosen inside a region -- is an error at the
target: binding the value to a name first holds it with no copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..identity_map import IdentityMap, IdentitySet
from ..parse import (TpyAugAssign, TpyAwait, TpyBinOp, TpyCall, TpyCoerce,
                     TpyFieldAccess, TpyFString, TpyMethodCall, TpyName,
                     TpySubscript, TpyUnaryOp)
from ..parse.nodes import (AugHeldPart, SubscriptDunder, TpyArrayLiteral,
                           TpyBoolLiteral, TpyBytesLiteral, TpyChainedCompare,
                           TpyDictComprehension, TpyDictLiteral,
                           TpyFloatLiteral,
                           TpyGeneratorExpression, TpyIfExpr,
                           TpyIntLiteral, TpyLambda, TpyListComprehension,
                           TpyListRepeat, TpyNamedExpr, TpyNoneLiteral,
                           TpySetComprehension, TpySetLiteral, TpySlice,
                           TpyStrLiteral, TpyTupleLiteral)
from ..type_def_registry import is_borrowing_view_type, is_free_copy_scalar
from ..typesys import (FloatLiteralType, IntLiteralType, LiteralType,
                       PendingNumType, is_numeric_type, param_takes_ownership,
                       unwrap_qualifiers)
from ..value_category import call_operands, peel_coerce, result_is_fresh
from .compatibility import TempRoot, TypeCompatibility
from .call_lends import may_hold_pointer
from .loop_frames import WriteProof
from .may_interrupt import NotInert, is_inert_type
from .own_copy import contains_reference_type

if TYPE_CHECKING:
    from ..parse import TpyExpr
    from ..typesys import TpyType
    from .context import SemanticContext


_REGIONS = (TpyIfExpr, TpyListComprehension, TpySetComprehension,
            TpyDictComprehension, TpyGeneratorExpression, TpyLambda,
            TpyChainedCompare)
# Regions whose value is a new container, generator or closure. One is held
# by its reader: a held one read by name would leave the reader's
# source-shaped render (an inline comprehension argument) evaluating it
# again.
_BUILDS = (TpyListComprehension, TpySetComprehension, TpyDictComprehension,
           TpyGeneratorExpression, TpyLambda)
_LITERALS = (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
             TpyBoolLiteral, TpyNoneLiteral)
# Containers built from their elements, each element stored by value.
_ELEMENT_STORES = (TpyTupleLiteral, TpyArrayLiteral, TpySetLiteral,
                   TpyDictLiteral, TpyListRepeat)


def _is_region(e: 'TpyExpr') -> bool:
    """`e` runs some of its parts conditionally, repeatedly or later."""
    return isinstance(e, _REGIONS) or (isinstance(e, TpyBinOp)
                                       and e.op in ("&&", "||"))


def _takes_by_value(reader: 'TpyExpr', part: 'TpyExpr') -> bool:
    """`reader` receives its operand `part` by value: as a container
    literal's element, or at a parameter or receiver that takes ownership
    (an `Own` argument, an `Own[Self]` method, an operator dunder's `Own`
    operand)."""
    if isinstance(reader, _ELEMENT_STORES):
        return True
    ops = call_operands(reader)
    return ops is not None and any(
        peel_coerce(a) is part and ops.takes_ownership(i)
        for i, a in ops.indexed())


def _is_receiver(parent: 'TpyExpr', e: 'TpyExpr') -> bool:
    return (isinstance(parent, (TpyFieldAccess, TpySubscript, TpyMethodCall))
            and parent.obj is e)


def _arms(e: 'TpyExpr') -> 'list[TpyExpr]':
    """The parts a conditional region's value is one of."""
    if isinstance(e, TpyIfExpr):
        return [e.then_expr, e.else_expr]
    if isinstance(e, TpyBinOp):
        return [e.left, e.right]
    return []


def _scalar(t: 'TpyType | None') -> bool:
    """A value nothing points into, held by value as a local holds it: a
    number of any width (`int` included), a free-copy scalar
    (`is_free_copy_scalar`), a literal read as its base type. A number
    whose type settles after its body (`PendingNumType`) counts, whatever
    it settles to."""
    if t is None:
        return False
    t = unwrap_qualifiers(t)
    if isinstance(t, LiteralType):
        t = t.base_type
    return (isinstance(t, (IntLiteralType, FloatLiteralType, PendingNumType))
            or is_numeric_type(t) or is_free_copy_scalar(t))


def _view(t: 'TpyType | None') -> bool:
    return t is not None and is_borrowing_view_type(unwrap_qualifiers(t))


class _RepeatProof(WriteProof):
    """Whether evaluating an expression again is observable: the walk
    `WriteProof` closes over calls, with no check point, no callee writing
    what a value of its operands could show (`list.pop`, `bytearray.pop`
    write; a builtin iterating an iterator advances it, `call_write`) and
    no rebinding (a walrus). What counts is an OPERATION that may run user
    code, not a value of a user type: reading, choosing or passing a value
    runs nothing, while every operand an operation runs runtime code on
    must be of an inert type (`is_inert_type`) -- a builtin call's
    arguments and receiver (`min` compares, `hash` hashes), an operator's
    operands, the truth test of a condition, the key a builtin subscript
    hashes and compares, an f-string part, a container literal's elements.
    A place read through (`ps[0].n` reading a record's field) runs nothing
    on what it holds. An `await` was bound to a local ahead of the
    statement (`_lift_nested_awaits`)."""

    admits_check_points = False

    def __init__(self, ctx: 'SemanticContext') -> None:
        super().__init__(ctx)
        # Each held node, with the part of the target it is under.
        self.held: 'IdentityMap[TpyExpr, str]' = IdentityMap()

    def passes(self, e: 'TpyExpr', as_place: bool) -> bool:
        try:
            if as_place:
                self.place(e)
            else:
                self.walk(e)
        except NotInert:
            return False
        return True

    def operands(self, e: 'TpyExpr', as_place: bool = False
                 ) -> 'list[tuple[TpyExpr, bool]]':
        """`InertWalk.operands`, a method's receiver read through as a
        place: the callee gets it in place (`self`), and the call is judged
        as the call it is. A receiver taken by ownership (`Own[Self]`) is
        a value moved in."""
        ops = super().operands(e, as_place)
        fi = None
        if isinstance(e, TpyMethodCall):
            fi = e.resolved_function_info
        elif isinstance(e, TpySubscript):
            fi = e.dunder(SubscriptDunder.GET)
        if (fi is not None and not fi.root.is_consuming and ops
                and ops[0][0] is e.obj):
            ops[0] = (e.obj, True)
        return ops

    def write_shows(self, e: 'TpyExpr') -> bool:
        """A write into `e` shows to a later read: it is or holds an object,
        or a view of one (a number or a `str` holds neither)."""
        t = self.ctx.get_expr_type(peel_coerce(e))
        return (t is None or contains_reference_type(unwrap_qualifiers(t))
                or may_hold_pointer(t))

    def rebind(self, name: str, t: 'TpyType | None') -> None:
        raise NotInert

    def typed(self, e: 'TpyExpr') -> None:
        """A value read, chosen or passed runs nothing of its own; the
        operations reading it check it (`operation`)."""

    def operation(self, operands: 'list[TpyExpr]') -> None:
        for op in operands:
            self.require(is_inert_type(self.ctx.get_expr_type(op)))

    def visit(self, e: 'TpyExpr') -> bool:
        if e in self.held:
            # A held comprehension, generator expression or lambda is
            # evaluated by its reader (`_HoldPlan.collect`).
            self.require(not isinstance(e, _BUILDS))
            return False
        if isinstance(e, TpyAwait):
            return False
        if isinstance(e, TpyCoerce):
            self.on_node(e)
            return True
        if isinstance(e, TpyIfExpr):
            self.operation([e.condition])
        elif isinstance(e, TpyBinOp):
            self.operation([e.left] if e.op in ("&&", "||")
                           else [e.left, e.right])
        elif isinstance(e, TpyUnaryOp):
            self.operation([e.operand])
        elif isinstance(e, (TpyFString, *_ELEMENT_STORES, *_BUILDS)):
            self.operation(e.children())
        elif isinstance(e, TpySubscript):
            self.key_operation(e)
        elif (isinstance(e, (TpyCall, TpyMethodCall))
              and e.macro_expansion is None
              and not (isinstance(e, TpyCall)
                       and (e.isinstance_type is not None
                            or e.cast_target_type is not None))):
            # A body runs its own code (and is held for it); a builtin
            # runs runtime code on its operands.
            self.operation([op for op, _ in self.operands(e)])
        return super().visit(e)

    def key_operation(self, e: 'TpySubscript') -> None:
        """A builtin subscript hashes and compares its key."""
        if e.dunder(SubscriptDunder.GET) is None:
            self.operation(e.index.children() if isinstance(e.index, TpySlice)
                           else [e.index])

    def place(self, e: 'TpyExpr') -> None:
        if e in self.held:
            return
        if isinstance(e, TpySubscript):
            self.key_operation(e)
        super().place(e)


class _HoldPlan:
    """The parts one augmented target holds (`aug_hold_parts`)."""

    def __init__(self, ctx: 'SemanticContext', compat: 'TypeCompatibility',
                 stmt: TpyAugAssign) -> None:
        self.ctx = ctx
        self.compat = compat
        self.stmt = stmt
        self.proof = _RepeatProof(ctx)
        # The receiver and the key parts as the target spells them.
        self.roots = IdentitySet()
        # Parts a held reader takes by value: moved into what it builds,
        # which points into none of them.
        self.consumed = IdentitySet()

    def type_of(self, e: 'TpyExpr') -> 'TpyType | None':
        return self.ctx.get_expr_type(e)

    # -- what to hold --

    def collect(self, e: 'TpyExpr', as_place: bool, role: str) -> None:
        """Hold the innermost nodes under `e` whose evaluation is observable
        when repeated, a region whole: a node is held when it stays
        observable with its observable parts held (`view(label())` holds
        both calls, `label().strip()` only the inner one)."""
        proof = self.proof
        if proof.passes(e, as_place):
            return
        if _is_region(e):
            self.check_region(e, role)
            proof.held[e] = role
            return
        consumed = False
        for op, op_place in proof.operands(e, as_place):
            self.collect(op, op_place, role)
            part = peel_coerce(op)
            if part in proof.held and _takes_by_value(e, part):
                # CONSUMPTION: the reader is held, so its one read passes
                # the part as decided. A value the part creates moves in
                # inline, so it is not held apart.
                if result_is_fresh(self.ctx, part):
                    del proof.held[part]
                    self.consumed.add(part)
                consumed = True
        if ((consumed or not proof.passes(e, as_place))
                and not isinstance(e, (TpyName, TpyCoerce, TpySlice))):
            for op, _ in proof.operands(e, as_place):
                part = peel_coerce(op)
                if isinstance(part, _BUILDS) and part in proof.held:
                    del proof.held[part]
            proof.held[e] = role

    def fresh(self, e: 'TpyExpr') -> bool:
        """`e`'s value is one it creates and that points into nothing: a
        region on every arm."""
        if _is_region(e):
            arms = _arms(e)
            return (all(self.fresh(a) for a in arms) if arms
                    else not isinstance(e, TpyChainedCompare))
        t = self.type_of(e)
        return (t is not None and result_is_fresh(self.ctx, e)
                and not may_hold_pointer(t))

    def check_region(self, e: 'TpyExpr', role: str) -> None:
        if not (_scalar(self.type_of(e)) or self.fresh(e)):
            raise self.region_error(e, role)

    def region_error(self, e: 'TpyExpr', role: str) -> Exception:
        part = (f"the {role}" if e in self.roots else f"a value in the {role}")
        if any(self.fresh(a) for a in _arms(e)):
            return self.ctx.error(
                f"{part} here is chosen inside a conditional expression "
                f"between a new value and existing storage and cannot be "
                f"evaluated once without a copy; bind it to a name first",
                self.stmt.target)
        kind, owner = (("view", "the value it views")
                       if _view(self.type_of(e))
                       else ("reference", "the referenced value"))
        return self.ctx.error(
            f"{part} here is a {kind} chosen inside a conditional "
            f"expression and cannot be evaluated once; bind {owner} to a "
            f"name first", self.stmt.target)

    def check_value(self, e: 'TpyExpr', role: str) -> None:
        """A held value part is a number, a view (held after what it
        points into), a region checked whole, or a value positively proven
        new (`result_is_fresh`). Anything else -- a bodyless binding's
        result, which may be an operand or an element in place -- cannot
        be held without a copy or a reference into storage the statement
        may change."""
        if self.holdable(e):
            return
        part = (f"the {role}" if e in self.roots else f"a value in the {role}")
        raise self.ctx.error(
            f"{part} here is evaluated once but is not known to be a new "
            f"value, so it cannot be held without a copy; bind it to a name "
            f"first", self.stmt.target)

    def check_receiver(self, target: 'TpySubscript') -> None:
        """A held receiver is read in place by both the read and the store,
        which a `__getitem__` / `__setitem__` taking it by ownership
        (`Own[Self]`) cannot share (BUGS.md#aug-consuming-subscript-dunder)."""
        for dunder in (SubscriptDunder.GET, SubscriptDunder.SET):
            fi = target.dunder(dunder)
            if fi is not None and fi.root.is_consuming:
                raise self.ctx.error(
                    f"the receiver here is evaluated once and read by both "
                    f"`__getitem__` and `__setitem__`, but `{fi.name}` takes "
                    f"it by ownership (`Own[Self]`); an augmented assignment "
                    f"through it is not supported", self.stmt.target)

    def setter_consumes(self, e: 'TpyExpr') -> bool:
        """`e` is the key as written of a subscript whose `__setitem__`
        takes its key by ownership, and a value the statement creates."""
        target = self.stmt.target
        if not isinstance(target, TpySubscript):
            return False
        key = (target.written_index if target.written_index is not None
               else target.index)
        setter = target.dunder(SubscriptDunder.SET)
        return (e is key and setter is not None
                and bool(setter.root.params)
                and setter.root.params[0].type is not None
                and param_takes_ownership(setter.root.params[0].type)
                and result_is_fresh(self.ctx, e))

    def holdable(self, e: 'TpyExpr') -> bool:
        """`check_value` admits a held non-receiver `e`."""
        t = self.type_of(e)
        return (_scalar(t) or _view(t) or _is_region(e)
                or result_is_fresh(self.ctx, e))

    def lift(self, roots: 'list[tuple[TpyExpr, bool]]') -> None:
        """A held value part not known to be new is held through its
        nearest enclosing part of the same root that is a number or a new
        value (`hist[int(input())]` holds the `int`, `xs[len(b.get())]`
        the `len`): its one evaluation reads the part, and the
        temporaries in it live through that full expression. Without one,
        `check_value` refuses the part."""
        held = self.proof.held
        lifted: 'IdentityMap[TpyExpr, str]' = IdentityMap()

        def visit(e: 'TpyExpr', as_place: bool, parent: 'TpyExpr',
                  chain: 'list[TpyExpr]') -> None:
            if e in held and _is_region(e):
                return
            for op, op_place in self.proof.operands(e, as_place):
                visit(op, op_place, e, chain + [e])
            if (e in held and not self.holdable(e)
                    and not _is_receiver(parent, e)):
                for a in reversed(chain):
                    if (not isinstance(a, (TpyName, TpyCoerce, TpySlice))
                            and (_scalar(self.type_of(a)) or self.fresh(a))):
                        lifted[a] = held[e]
                        break

        for e, as_place in roots:
            visit(e, as_place, self.stmt.target, [])
        for a, role in lifted.items():
            held[a] = role
        # An outer lifted part drops an inner one with the rest.
        for a in lifted:
            self.drop_under(a)

    def drop_under(self, e: 'TpyExpr', as_place: bool = False) -> None:
        """Hold nothing inside `e`, which is evaluated once as a whole."""
        for op, op_place in self.proof.operands(e, as_place):
            part = peel_coerce(op)
            self.proof.held.pop(part, None)
            self.proof.held.pop(op, None)
            self.drop_under(op, op_place)

    # -- what a held view or reference points into --

    def points(self, e: 'TpyExpr') -> bool:
        """A held `e` may point into storage: it is no scalar and no value
        it creates."""
        return not (_scalar(self.type_of(e)) or self.fresh(e))

    def temps(self, e: 'TpyExpr', role: str) -> 'list[TpyExpr]':
        """The temporaries the storage held `e` denotes or views lives in,
        which the statement must hold too (`borrow_temp_roots`, every
        one). A held part lives to the end of the statement's block, a name
        or a literal outside it."""

        def stop(x: 'TpyExpr') -> 'list[TempRoot] | None':
            if x in self.proof.held or isinstance(x, (TpyName, TpyNamedExpr,
                                                      TpyAwait, *_LITERALS)):
                return []
            if isinstance(x, _BUILDS):
                raise self.unkept_temp_error(role)
            if self.fresh(x):
                return [] if _scalar(self.type_of(x)) else [TempRoot(x)]
            if _is_region(x):
                if any(self.compat.borrow_temp_roots(a, stop=stop)
                       for a in _arms(x)):
                    raise self.region_error(x, role)
                return []
            return None

        roots = self.compat.borrow_temp_roots(e, stop=stop, through=True)
        if any(r.in_tuple for r in roots):
            raise self.unkept_temp_error(role)
        return [r.expr for r in roots]

    def unkept_temp_error(self, role: str) -> Exception:
        # A comprehension's or a tuple literal's temporary is not held yet
        # (BUGS.md#aug-held-view-unkept-temp-rejects).
        return self.ctx.error(
            f"the {role} here views a temporary that cannot be kept until "
            f"the store; bind it to a name first", self.stmt.target)

    def back(self) -> None:
        held = self.proof.held
        for e, role in list(held.items()):
            if self.points(e):
                for t in self.temps(e, role):
                    if t not in held and t not in self.consumed:
                        held[t] = role

    # -- the parts, in evaluation order --

    def ordered(self, roots: 'list[tuple[TpyExpr, bool]]'
                ) -> 'tuple[AugHeldPart, ...]':
        out: list[AugHeldPart] = []

        def visit(e: 'TpyExpr', as_place: bool, parent: 'TpyExpr') -> None:
            held = e in self.proof.held
            if not (held and _is_region(e)):
                for op, op_place in self.proof.operands(e, as_place):
                    visit(op, op_place, e)
            if held:
                receiver = _is_receiver(parent, e)
                if not receiver:
                    self.check_value(e, self.proof.held[e])
                elif parent is self.stmt.target and isinstance(
                        parent, TpySubscript):
                    self.check_receiver(parent)
                out.append(AugHeldPart(
                    e, receiver=receiver,
                    setter_consumes=(not receiver
                                     and self.setter_consumes(e))))

        for e, as_place in roots:
            visit(e, as_place, self.stmt.target)
        return tuple(out)


def holds_by_value(t: 'TpyType | None') -> bool:
    """A held part of type `t` is a number, a free-copy scalar or a view,
    so it is held by value rather than bound by reference into what it was
    read from. Asked at lowering, on the settled type: a literal-seeded
    number's type settles only once its function body has been analyzed."""
    return _scalar(t) or _view(t)


def aug_hold_parts(ctx: 'SemanticContext', compat: 'TypeCompatibility',
                   stmt: TpyAugAssign) -> 'tuple[AugHeldPart, ...]':
    """The parts of `t[k] op= v` / `o.n op= v`'s target the statement
    evaluates once and reads by name: the receiver read as a place, a
    subscript's key as written as a value. An error when a part cannot be
    held without a copy or a dangling view."""
    target = stmt.target
    roots: list[tuple['TpyExpr', bool, str]] = []
    if isinstance(target, TpySubscript):
        key = (target.written_index if target.written_index is not None
               else target.index)
        roots.append((target.obj, True, "receiver"))
        roots += [(p, False, "subscript key") for p in
                  (key.children() if isinstance(key, TpySlice) else [key])]
    elif isinstance(target, TpyFieldAccess) and target.names_type is None:
        roots.append((target.obj, True, "receiver"))
    plan = _HoldPlan(ctx, compat, stmt)
    plan.roots.update(peel_coerce(e) for e, _, _ in roots)
    for e, as_place, role in roots:
        plan.collect(e, as_place, role)
    plan.lift([(e, as_place) for e, as_place, _ in roots])
    plan.back()
    return plan.ordered([(e, as_place) for e, as_place, _ in roots])
