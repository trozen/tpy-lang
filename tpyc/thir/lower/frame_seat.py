"""What a statement keeps of each temporary and how long its seat lives:
decided once, on entering the statement (`plan_keeps`), from the bound each
expression is handed (`bound_rule`), and read at the seat (`planned`)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Callable, NamedTuple

from ...codegen_cpp.context import CodeGenError
from ...codegen_cpp.forms import LoopBinding, binds_handed_over_step, loop_binding_kind
from ...parse.nodes import (
    TpyArrayLiteral, TpyAssert, TpyAssign, TpyAugAssign, TpyAwait,
    TpyBreak, TpyContinue, TpyDelAttr, TpyDelItem, TpyDelVar, TpyGlobal,
    TpyImport, TpyNonlocal, TpyPassStmt, TpyRaise, TpyReturn, TpyTry,
    TpyTupleUnpack, TpyVarDecl, TpyYield, TpyExprStmt, TpyIf, TpyMatch,
    TpyNestedDef, TpyWhile, TpyWith, TpyBinOp, TpyBoolLiteral,
    TpyBytesLiteral, TpyCall, TpyCallLike, TpyChainedCompare, TpyCoerce,
    TpyDictComprehension, TpyDictLiteral, TpyExpr, TpyFieldAccess,
    TpyFloatLiteral, TpyForEach, TpyFString, TpyGeneratorExpression,
    TpyIfExpr, TpyIntLiteral, TpyLambda, TpyListComprehension, TpyListRepeat,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNoneLiteral, TpySetComprehension,
    TpySetLiteral, TpySlice, TpyStarUnpack, TpyStmt, TpyStrLiteral,
    TpySubscript, TpyTupleLiteral, TpyTypeParamConstruct, TpyUnaryOp,
    TpyVarargPack, comp_clause_children, comp_free_reads,
    iter_capture_bindings,
)
from ...identity_map import IdentityMap
from ..nodes import SeatLifetime
from ...emitted_members import resolve_protocol_type
from ...typesys import (
    OwnType, PendingViewType, param_takes_ownership,
    protocol_param_alternatives,
    peel_binding, unwrap_qualifiers, unwrap_readonly, unwrap_send_sync,
)
from ...modules.type_resolution import get_iterable_element_type
from ...sema.source_records import read_record
from ...value_category import (
    binds_buffer_view, carries_borrow, element_borrow_form,
    frame_param_lends, frame_receiver_temp, frame_temp_arg_source,
    holds_reference_by_pointer,
    materializing_temp_source,
    peel_coerce, pointer_leaf_tuple,
)
from ..param_contract import (
    HandleUse, awaited_result_keeps, frame_captured, handle_use,
    instantiated_param, own_param_takes, owned_tuple_leaves,
)
from ...sema.call_lends import handed_over_record, result_may_borrow
from .predicates import (
    _resolved_viewfam_value, bytes_literal_view_slot, for_consuming_shape,
    resolved_loop_elem_type,
)
from ...type_def_registry import (
    is_borrowing_view_type, is_bytes_view_type, is_str_view_type,
)

if TYPE_CHECKING:
    from ...parse.nodes import TpyComprehensionGenerator
    from ...typesys import FunctionInfo, TpyType
    from ...value_category import ValueCategoryAnalyzer
    from .context import _LowerCtx

# A collection literal or comprehension: at a protocol parameter it has no
# C++ type but the one deduced from it.
_FRESH_COLLECTION_NODES = (
    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
)


class Hand(NamedTuple):
    """A sub-expression and how long its value lives."""
    node: object
    bound: HandleUse
    # Needed past its statement.
    lives_on: bool
    # The callee, parameter index (-1: the receiver) and argument it is
    # delivered to: the seat decision reads it (`_own_argument`,
    # `_direct_frame_operand`, `_captured_as_is`, `Keep.taken`).
    taker: 'tuple | None'
    # Its storage is part of what its parent's value holds: an operand the
    # result keeps (a callee frame's argument, a borrow the call returns),
    # a select's arm, an element a container or tuple holds as it is. A
    # value moved, copied or consumed is not held.
    held: bool = False
    # The value of a `return` or `yield` statement of a resumable body: a
    # handle built in it leaves the frame, which dies before it -- except
    # a direct operand of a call the value awaits (`walk_regions`).
    leaves: bool = False


def walk_hands(roots: 'Iterable[Hand]', analyzer: 'ValueCategoryAnalyzer',
               *, through: 'HandleUse | None' = None) -> 'Iterator[Hand]':
    """Every hand under `roots`, descending through `bound_rule` -- with
    `through`, only below a hand of that bound."""
    stack = list(roots)
    while stack:
        hand = stack.pop()
        yield hand
        if through is None or hand.bound is through:
            stack.extend(operands_of(hand, analyzer))


class Keep(NamedTuple):
    """The plan's answer for one sub-expression of a statement
    (`plan_keeps`)."""
    # Where its seat lives, if one is asked for it: ENCLOSING (FRAME in a
    # resumable body) for a head's fresh object a name the statement binds
    # and reads after it borrows (sema's `head_enclosed_root`).
    lifetime: SeatLifetime
    # The statement keeps it until it ends: a compound head does (sema's
    # `head_kept_argument`), or a coroutine or generator call the statement
    # consumes borrows it inside its argument.
    kept: bool
    # It borrows out of what a compound head keeps (sema's
    # `head_kept_borrows`).
    borrows_kept: bool
    taker: 'tuple | None'
    # A direct operand of a coroutine or generator call an `await` of the
    # statement runs to completion (`walk_regions`): that call's frame
    # captures it, so this frame seats it only where it is a temporary.
    awaited: bool = False
    # A direct operand of a coroutine or generator call of a resumable body
    # whose frame holds it (`held_temp`): not one that leaves the frame
    # with a `return` / `yield` value (`Hand.leaves`).
    taken: bool = False
    # Its FRAME seat is a frame holder of its own whatever reads it: it is
    # kept, taken, or a call's own argument (`_own_call_argument`). A FRAME
    # entry without one that only an operand rule seats (a held operand's
    # fresh root) is a temp of its statement, so a resumable body's frame
    # is the one it has without those rules.
    frame_holder: bool = False


class KeepPlan:
    """What a statement (or a region lowered apart from it: a lambda body)
    keeps of each sub-expression, decided once by `plan_keeps` -- on
    entering the statement, and for an expression its roots do not reach
    (a branch condition a frame block re-reads, a top-level initializer)
    on entering that expression -- and read at the seat (`planned`)."""
    __slots__ = ("entries", "in_frame", "crosses")

    def __init__(self, *, in_frame: bool, crosses: bool) -> None:
        self.entries = IdentityMap()
        self.in_frame = in_frame
        self.crosses = crosses

    def covers(self, node: object) -> bool:
        return peel_coerce(node) in self.entries

    def stand_in(self, new: object, old: object) -> None:
        """A node the lowering builds in place of `old` (a forwarded
        alias's parameter read) holds what `old` holds."""
        if self.covers(old):
            self.entries[peel_coerce(new)] = self.get(old)

    def get(self, node: object) -> Keep:
        """The entry of `node`, keyed by the argument under its conversion
        (`peel_coerce`). Every node lowered under a plan is one its walk
        reached; with no plan (`EMPTY_PLAN`) nothing is kept past its full
        expression."""
        hit = self.entries.get(peel_coerce(node))
        if hit is not None:
            return hit
        assert self is EMPTY_PLAN, (
            f"keep plan has no entry for {type(node).__name__} at line "
            f"{getattr(getattr(node, 'loc', None), 'line', '?')}")
        return Keep(SeatLifetime.FULL_EXPRESSION, False, False, None)


EMPTY_PLAN = KeepPlan(in_frame=False, crosses=False)


def plan_keeps(plan: KeepPlan, roots: 'tuple[Hand, ...]',
               analyzer: 'ValueCategoryAnalyzer') -> None:
    """THE keep decision: for each sub-expression of the position `roots`
    hand on not yet in `plan`, how long a seat of it lives and whether the
    statement keeps it, from how long its value lives (`bound_rule`) and
    whether a suspension of the frame falls while it is needed
    (`plan.crosses`: a compound head whose state crosses one, an awaited
    operand) -- each input for the statement as a whole, and inside a
    comprehension for its own region (`walk_regions`). A temporary a
    coroutine or generator call's argument borrows
    (`frame_arg_borrowed_temps`) is kept: for the statement, or in a frame
    holder where the call keeps it past the statement of a resumable body
    (`_lifetime`). A handle that outlives that seat dangles as its sync
    twin does (BUGS.md#frame-handle-outlives-fresh-argument)."""
    in_frame = plan.in_frame
    walked: 'list[tuple[Hand, bool]]' = []
    # The nodes that leave the frame with a `return` / `yield` value.
    out_of_body = IdentityMap()
    awaited_operands = IdentityMap()
    nested = IdentityMap()
    for hand, cross, leaves, awaited in walk_regions(
            roots, analyzer, plan.crosses):
        if plan.covers(hand.node):
            continue
        walked.append((hand, cross))
        if leaves:
            out_of_body[peel_coerce(hand.node)] = True
        if awaited and _direct_frame_operand(hand):
            awaited_operands[peel_coerce(hand.node)] = True
        elif awaited:
            _refuse_awaited_collection_literal(hand)
        fi, index, arg = hand.taker or (None, None, None)
        # The argument as handed, not what a coercion of it wraps: the
        # coercion decides what of it the frame borrows.
        if arg is not hand.node or index is None or not _direct_frame_operand(
                hand):
            continue
        if index == -1 and fi.is_consuming:
            # The frame owns its receiver.
            continue
        if index == -1 and fi.is_generator and frame_receiver_temp(
                hand.node, analyzer):
            # A generator frame holds its receiver by reference: a
            # constructed one lives for the statement that builds the
            # handle, as sema's dangle warnings read it.
            nested[peel_coerce(hand.node)] = True
        if _frame_lends(fi, index, hand.node, analyzer):
            for t in frame_arg_borrowed_temps(hand.node, fi, analyzer):
                nested[peel_coerce(t)] = True
    head_kept = analyzer.head_kept_args
    head_borrows = analyzer.head_kept_borrows
    longest = IdentityMap()
    for hand, cross in walked:
        prev = longest.get(hand.node)
        if prev is None or _longer(hand, prev[0]):
            longest[hand.node] = (hand, cross)
    for hand, cross in longest.values():
        n = hand.node
        if plan.covers(n):
            continue
        enclosed = n in analyzer.head_enclosed_roots or (
            peel_coerce(n) in analyzer.head_enclosed_roots)
        kept = (n in head_kept or peel_coerce(n) in head_kept
                or peel_coerce(n) in nested)
        escapes = peel_coerce(n) in out_of_body
        awaited = in_frame and peel_coerce(n) in awaited_operands
        lifetime = (SeatLifetime.FULL_EXPRESSION
                    if awaited and not enclosed
                    and _captured_as_is(hand, analyzer)
                    else _lifetime(hand, kept, in_frame, cross, enclosed))
        taken = in_frame and not escapes and _direct_frame_operand(hand)
        plan.entries[peel_coerce(n)] = Keep(
            lifetime, kept,
            n in head_borrows or peel_coerce(n) in head_borrows,
            hand.taker, awaited, taken,
            lifetime is SeatLifetime.FRAME and (
                kept or taken or _own_call_argument(hand)))


def _own_argument(hand: Hand) -> bool:
    """Whether `hand` is an argument its call is handed as it is (its
    taker's own argument)."""
    return (hand.taker is not None
            and peel_coerce(hand.taker[2]) is peel_coerce(hand.node))


def _own_call_argument(hand: Hand) -> bool:
    """Whether `hand` is an argument its call is handed as it is, at a
    parameter (not the receiver)."""
    return (_own_argument(hand) and hand.taker[1] is not None
            and hand.taker[1] >= 0)


def _direct_frame_operand(hand: Hand) -> bool:
    """THE test of a direct operand of a coroutine or generator call: an
    argument (`_own_argument`) whose callee's own frame captures it."""
    return _own_argument(hand) and frame_captured(hand.taker[0])


def _refuse_awaited_collection_literal(hand: Hand) -> None:
    """Refuse a fresh collection literal or comprehension handed directly
    to a protocol parameter of a call that is no coroutine or generator but
    that the statement awaits: no frame holds it across the suspension, and
    the awaited handle's member type has nothing to deduce it from."""
    fi, index, _arg = hand.taker
    node = peel_coerce(hand.node)
    if not (isinstance(node, _FRESH_COLLECTION_NODES)
            and index is not None and 0 <= index < len(fi.params)
            and protocol_param_alternatives(
                fi.params[index].type, resolve_protocol_type) is not None):
        return
    raise CodeGenError(
        "awaiting a coroutine with a protocol-typed parameter does not yet "
        "support a collection literal argument; pass a named iterable "
        "instead", loc=getattr(node, "loc", None))


def _captured_as_is(hand: Hand, analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Whether the frame of the awaited coroutine or generator call `hand`
    is a direct argument of captures it as it is (a prvalue by value, an
    lvalue by reference), needing no seat here: anything but a temporary
    it would reference (`frame_temp_arg_source`), which the general rule
    seats. Not the receiver, which the general rule seats too."""
    fi, index, arg = hand.taker
    if index is None or not 0 <= index < len(fi.params):
        return False
    return frame_temp_arg_source(arg, instantiated_param(
        fi.params[index].type, arg, analyzer), analyzer) is None


def _lifetime(hand: Hand, kept: bool, in_frame: bool, crosses: bool,
              enclosed: bool) -> SeatLifetime:
    """In a resumable body, storage a value that outlives the statement
    holds (`Hand.held`: a callee frame's argument, a borrow its result
    keeps), or one needed across a suspension inside the statement (which
    a `case` block's local does not survive), lives on the frame, and so
    does an enclosed root. Otherwise what the statement keeps lives for
    the statement, or in the enclosing scope where a binding read after
    the statement borrows it, and anything else for its full expression.
    Outside a resumable body a value kept past its statement is the
    accepted borrow-past-statement class. A value moved, copied or
    consumed holds no storage of its operand, so it extends nothing."""
    if enclosed:
        return SeatLifetime.FRAME if in_frame else SeatLifetime.ENCLOSING
    if in_frame and (hand.held or kept) and (
            hand.bound is HandleUse.ESCAPES or hand.lives_on or crosses):
        return SeatLifetime.FRAME
    if kept:
        return SeatLifetime.STATEMENT
    return SeatLifetime.FULL_EXPRESSION


def _frame_lends(fi: 'FunctionInfo', index: int, arg: TpyExpr,
                 analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Whether the frame `fi` builds borrows its argument `index` (-1: the
    receiver), at the type the parameter holds there."""
    if index == -1:
        return True
    if not 0 <= index < len(fi.params):
        return False
    lent = instantiated_param(fi.params[index].type, arg, analyzer)
    return lent is not None and frame_param_lends(lent)


def walk_regions(roots: 'Iterable[Hand]', analyzer: 'ValueCategoryAnalyzer',
                 crosses: bool
                 ) -> 'Iterator[tuple[Hand, bool, bool, bool]]':
    """`walk_hands`, each hand with whether a suspension falls while it
    is needed, whether it leaves the frame with a `return` / `yield` value
    (`Hand.leaves`) and whether it is a direct operand of a call an
    `await` of the statement runs to completion (`_own_argument`). Where
    that call is a coroutine or generator (`_direct_frame_operand`) the
    operand does not leave: the awaited call's own frame holds it; any
    other awaited call returns before the suspension, so its operands are
    the statement's. No suspension falls inside a comprehension (its fresh
    arguments live in its own block); a generator expression's body is its
    own function's, and what it is created from -- its first source, its
    captures' reads -- is read where it is created."""
    stack: 'list[tuple[Hand, bool, bool, bool]]' = [
        (h, crosses, h.leaves, False) for h in roots]
    # Every call an `await` of the statement runs.
    run = IdentityMap()
    while stack:
        hand, cross, leaves, direct = stack.pop()
        yield (hand, cross,
               leaves and not (direct and _direct_frame_operand(hand)), direct)
        n = hand.node
        first = None
        created: 'tuple[object, ...]' = ()
        if isinstance(n, (TpyListComprehension, TpySetComprehension,
                          TpyDictComprehension, TpyGeneratorExpression)):
            first = n.generators[0].iterable
        if isinstance(n, TpyGeneratorExpression):
            created = (first, *n.frame_captures)
        if isinstance(n, TpyAwait):
            run[peel_coerce(n.value)] = True
        for c in operands_of(hand, analyzer):
            below = direct if isinstance(n, TpyCoerce) else (
                n in run and _own_argument(c))
            if first is None or any(c.node is k for k in created):
                stack.append((c, cross, leaves, below))
            elif not created:
                stack.append((c, False, leaves, below))


class BoundScope:
    """The position being lowered -- its root nodes, each with how long its
    value lives (`Hand`) -- and the keep plan of the statement it belongs
    to (`plan_keeps`)."""
    __slots__ = ("roots", "plan")

    def __init__(self, roots: 'tuple[Hand, ...]',
                 plan: KeepPlan = EMPTY_PLAN) -> None:
        self.roots = roots
        self.plan = plan


def planned(node: object, lc: '_LowerCtx') -> Keep:
    """THE read of the keep plan: what the statement being lowered keeps of
    `node` and how long its seat lives (`plan_keeps`)."""
    return lc.bound_roots.plan.get(node)


def frame_arg_borrowed_temps(a: TpyExpr, fi: 'FunctionInfo | None',
                             analyzer: 'ValueCategoryAnalyzer'
                             ) -> 'list[TpyExpr]':
    """The fresh temporaries nested in argument `a` of coroutine or
    generator call `fi` that the argument's value borrows, at any depth:
    what the call keeps of `a` through each operand's contract
    (`walk_hands`). Not one the call takes directly (seated by the
    argument's own row) or over an `Own` parameter (moved), a conversion
    (its operand is asked), nor one a compound head keeps already."""
    out: list[TpyExpr] = []
    # A tuple an element read copies a pointer out of: the read borrows
    # what that pointer points to, not the tuple's own storage.
    carriers: list[TpyExpr] = []
    for hand in walk_hands((Hand(a, HandleUse.ESCAPES, False, (fi, None, a)),),
                           analyzer, through=HandleUse.ESCAPES):
        carrier = pointer_leaf_tuple(hand.node, analyzer)
        if carrier is not None:
            carriers.append(carrier)
        if any(hand.node is c for c in carriers):
            continue
        taker, index = (hand.taker[0], hand.taker[1]) \
            if hand.taker is not None else (None, None)
        taken = taker is not None and (frame_captured(taker) or (
            index is not None and 0 <= index < len(taker.params)
            and taker.params[index].type is not None
            and param_takes_ownership(taker.params[index].type)))
        # A literal read in its static storage holds nothing, and a view is
        # held by value: what a view's borrower keeps is what it points into.
        if hand.node is not a and hand.bound is HandleUse.ESCAPES and (
                not taken) and peel_coerce(hand.node) is hand.node and (
                not _static_literal(hand, analyzer)) and (
                not is_view_value(hand.node, analyzer)) and (
                materializing_temp_source(hand.node, analyzer)) and (
                hand.node not in analyzer.head_kept_args):
            out.append(hand.node)
    return out


def _static_literal(hand: Hand, analyzer: 'ValueCategoryAnalyzer') -> bool:
    """A `str` literal, or a `bytes` literal its taker reads through its
    view (`bytes_literal_view_slot`): rendered over static storage."""
    n = hand.node
    if isinstance(n, TpyStrLiteral):
        return True
    if not isinstance(n, TpyBytesLiteral) or hand.taker is None:
        return False
    fi, index, arg = hand.taker
    if index == -1:
        return bytes_literal_view_slot(fi, analyzer.get_expr_type(n),
                                       receiver=True) is not None
    return (index is not None and 0 <= index < len(fi.params)
            and bytes_literal_view_slot(fi, instantiated_param(
                fi.params[index].type, arg, analyzer)) is not None)


def is_view_value(e: TpyExpr, analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Whether `e` is a view as sema resolved it (a `str` / `bytes` value
    pending its form at the form it took): held by value, so what a
    borrower of it keeps is what it points into, never the view itself."""
    t = analyzer.get_expr_type(e)
    if t is None:
        return False
    t = peel_binding(t)
    viewfam = _resolved_viewfam_value(t, analyzer)
    if viewfam is not None:
        return is_str_view_type(viewfam) or is_bytes_view_type(viewfam)
    return is_borrowing_view_type(t)


def bound_rule(e: TpyExpr, bound: HandleUse, lives_on: bool,
               analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    """THE classification: how long each operand of `e` lives, given `e`'s
    own `bound` -- one rule per expression kind (`_BOUND_RULES`). DELIVER:
    a call, an operator, a subscript, a field or property read, a
    conversion hand each operand
    over by the parameter contract of the callee sema resolved
    (`handle_use`) -- with none (a builtin scalar operator, an f-string
    part), kept by the result -- and what only the result keeps is
    consumed where the result cannot borrow it (`deliver`). PASS: a
    select's arm, a literal's or a comprehension's element
    live as `e` does, and so does a `*` spread's operand. ESCAPE: a
    walrus, a lambda body. Statement positions are `stmt_bound`'s."""
    return _BOUND_RULES[type(e)](e, bound, lives_on, analyzer)


def operands_of(hand: 'Hand', analyzer: 'ValueCategoryAnalyzer'
                 ) -> 'list[Hand]':
    """The hands `bound_rule` gives the operands of `hand`'s node; an
    operand handed on with no callee of its own (a literal's element)
    keeps its parent's taker."""
    n, bound, lives_on, taker = hand.node, hand.bound, hand.lives_on, \
        hand.taker
    if not isinstance(n, TpyExpr):
        return []
    owned = _owned_leaves(n, taker)
    return [Hand(c.node, HandleUse.ESCAPES, False,
                 c.taker if c.taker is not None else taker)
            if any(c.node is o for o in owned)
            and not copied_out(c.node, analyzer)
            else c._replace(taker=c.taker if c.taker is not None else taker)
            for c in bound_rule(n, bound, lives_on, analyzer)]


def _owned_leaves(n: TpyExpr, taker: 'tuple | None') -> 'list[TpyExpr]':
    """The leaves of `n`, handed to a tuple parameter that takes some over,
    that escape into the callee (`owned_tuple_leaves`)."""
    if taker is None:
        return []
    fi, index, operand = taker
    if operand is not n or index is None:
        return []
    return owned_tuple_leaves(fi, index, n) or []


def bound_in(roots: 'tuple[Hand, ...]', target: object,
             analyzer: 'ValueCategoryAnalyzer'
             ) -> 'tuple[HandleUse, bool, tuple | None]':
    """How long `target`'s value lives under `roots` (each a node with its
    bound), descending through `bound_rule`."""
    found: 'Hand | None' = None
    for hand in walk_hands(roots, analyzer):
        if hand.node is target and (found is None or _longer(hand, found)):
            found = hand
    if found is None:
        return HandleUse.ESCAPES, False, None
    return found.bound, found.lives_on, found.taker


def deliver(e: object, fi: 'FunctionInfo | None', index: 'int | None',
            operand: object, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'Hand':
    """`operand` handed to parameter `index` of `fi` (-1: the receiver;
    None: no parameter), as that parameter's contract says (with no
    callee, kept by the result); what only the result keeps is consumed
    where `e`'s result cannot borrow it (`result_may_borrow`)."""
    if fi is not None and fi.root.keeps_no_argument:
        # It keeps no argument, whatever parameter one binds (`print`).
        use = HandleUse.CONSUMED
    elif index is None:
        use = HandleUse.ESCAPES
    elif fi is None:
        use = HandleUse.KEPT_BY_RESULT
    else:
        use = handle_use(fi, index, e if isinstance(e, TpyExpr) else None,
                         analyzer,
                         operand if isinstance(operand, TpyExpr) else None)
    if use is HandleUse.KEPT_BY_RESULT and not result_may_borrow(
            e, fi, analyzer):
        use = HandleUse.CONSUMED
    if use is HandleUse.ESCAPES and copied_out(operand, analyzer):
        use = HandleUse.CONSUMED
    kept = use is HandleUse.KEPT_BY_RESULT
    # What escapes into the callee (stored, kept by a value nothing is
    # known of) holds its storage, unless an `Own` parameter takes it over.
    held = (kept or use is HandleUse.ESCAPES) and not _taken_over(
        fi, index, operand, analyzer)
    return Hand(operand, bound if kept else use, kept and lives_on,
                (fi, index, operand), held)


def _taken_over(fi: 'FunctionInfo | None', index: 'int | None',
                operand: object, analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Parameter `index` of `fi` takes its argument over whole: an `Own`
    parameter (`own_param_takes`), or an owning element slot it is handed
    over to (`handed_over_record`)."""
    return handed_over_record(operand, analyzer) or own_param_takes(
        fi, index, operand if isinstance(operand, TpyExpr) else None)


def copied_out(value: object, analyzer: 'ValueCategoryAnalyzer', *,
               capture_of: 'TpyLambda | None' = None) -> bool:
    """THE copy rule: `value` is handed on as a copy holding no borrow
    when sema's verdict copies every part of it that could point -- the
    owning sink's source record, or a lambda's capture verdict
    (`capture_of`) -- and the copy itself may hold no pointer."""
    if value is None:
        return False
    t = analyzer.get_expr_type(value)
    if t is None:
        return False
    if capture_of is not None:
        # A capture is a name read (`comp_free_reads`).
        if getattr(value, "name", None) in capture_of.owned_captures:
            return not carries_borrow(t)
        if binds_buffer_view(t):
            # No copy of its own (`str` would be COST): a copied view.
            return False
    else:
        rec = read_record(analyzer.ctx, value)
        if rec is not None and (rec.leaves or rec.copies_whole):
            return rec.copies_every_leaf and not carries_borrow(t)
    if isinstance(peel_binding(t), PendingViewType):
        return False
    return (t.is_value_type() and not holds_reference_by_pointer(t)
            and not carries_borrow(t))



def _leaf(e: TpyExpr, bound: HandleUse, lives_on: bool,
          analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    return ()


def _name(e: TpyExpr, bound: HandleUse, lives_on: bool,
          analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # A class named as a callable value is lowered as the lambda sema built
    # for it.
    if e.factory_expansion is not None:
        return (Hand(e.factory_expansion, bound, lives_on, None, True),)
    return ()


def _pass(e: TpyExpr, bound: HandleUse, lives_on: bool,
          analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    return tuple(_element(c, bound, lives_on, analyzer)
                 for c in e.children())


def _element(c: TpyExpr, bound: HandleUse, lives_on: bool,
             analyzer: 'ValueCategoryAnalyzer') -> 'Hand':
    """An element that lives as its container does, unless the container
    holds a copy of it (`copied_out`)."""
    if bound is not HandleUse.CONSUMED and copied_out(c, analyzer):
        return Hand(c, HandleUse.CONSUMED, False, None)
    return Hand(c, bound, lives_on, None, True)


def _escape(e: TpyExpr, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    return tuple(Hand(c, HandleUse.ESCAPES, False, None) for c in e.children())


def _lambda(e: TpyExpr, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # Its body's reads of what it captures (sema's `captured_names`, a
    # nested lambda's included), which live as the lambda value does,
    # unless the closure holds a copy of its own (`copied_out`).
    captured = set(e.captured_names)
    return tuple(
        Hand(n, HandleUse.CONSUMED, False, None)
        if e.captures_by_value and copied_out(n, analyzer, capture_of=e)
        else Hand(n, bound, lives_on, None, True)
        for n in comp_free_reads(e) if n.name in captured)


def _walrus(e: TpyExpr, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # Its value escapes into the name it binds, and what it evaluates to is
    # the read of that name after the binding (`TpyNamedExpr.read`).
    hands = _escape(e, bound, lives_on, analyzer)
    if e.read is not None:
        hands += (Hand(e.read, bound, lives_on, None, True),)
    return hands


def _no_callee(e: TpyExpr, bound: HandleUse, lives_on: bool,
               analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    return tuple(deliver(e, None, 0, c, bound, lives_on, analyzer)
                 for c in e.children())


def _call(e: TpyExpr, bound: HandleUse, lives_on: bool,
          analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    if e.macro_expansion is not None:
        return (Hand(e.macro_expansion, bound, lives_on, None, True),)
    if isinstance(e, TpyCall) and e.dunder_call is not None:
        # `obj(args)` is lowered as `obj.__call__(args)`.
        return (Hand(e.dunder_call, bound, lives_on, None, True),)
    fi = e.resolved_function_info
    names = [p.name for p in fi.params] if fi is not None else []
    hands: list = []
    args = list(e.args)
    if isinstance(e, TpyMethodCall):
        callee = (e.subscript_callee if e.subscript_callee is not None
                  else e.obj)
        if e.unbound_self_parent_type is not None and args:
            # `Base.method(self, ...)`: the first argument is the receiver.
            hands.append(Hand(callee, HandleUse.CONSUMED, False, None))
            callee = args.pop(0)
        hands.append(deliver(e, fi, -1, callee, bound, lives_on, analyzer))
    else:
        hands.append(Hand(e.func, HandleUse.CONSUMED, False, None))
        if e.subscript_callee is not None:
            hands.append(Hand(e.subscript_callee, HandleUse.CONSUMED,
                              False, None))
    spread = False
    for i, a in enumerate(args):
        # An unresolved callee (a callable value) may keep what it is
        # given; past a `*` spread no argument has a known parameter.
        at = i if fi is not None and not spread else None
        if isinstance(a, TpyStarUnpack):
            spread = True
            at = (i if at is not None and i < len(fi.params)
                  and fi.params[i].is_variadic else None)
        hands.append(deliver(e, fi, at, a, bound, lives_on, analyzer))
    hands += [deliver(e, fi, names.index(k) if k in names else None, v,
                      bound, lives_on, analyzer)
              for k, v in e.kwargs.items()]
    if e.double_star_unpack is not None:
        hands.append(Hand(e.double_star_unpack, HandleUse.ESCAPES, False,
                          None))
    if isinstance(e, TpyCall):
        # `hasattr` / `getattr` with a default are lowered over the
        # `__getattr__` call sema built.
        hands += [Hand(c, bound, lives_on, None, True)
                  for c in (e.dyn_hasattr_call, e.dyn_getattr_default_call)
                  if c is not None]
    return tuple(hands)


def _binop(e: TpyExpr, bound: HandleUse, lives_on: bool,
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    if e.op in ("&&", "||"):
        # A select: either operand is the value, as an `if` arm is.
        return tuple(Hand(c, bound, lives_on, None, True)
                     for c in e.children())
    if e.contains_call is not None:
        # `x in h` lowers as the call sema built, `h.__contains__(x)`.
        return (Hand(e.contains_call, bound, lives_on, None),)
    if e.resolved_contains is not None:
        fi = e.resolved_contains
        return (deliver(e, fi, 0, e.left, bound, lives_on, analyzer),
                deliver(e, fi, -1, e.right, bound, lives_on, analyzer))
    rb = e.resolved_binop
    fi = rb.method if rb is not None else None
    left, right = (0, -1) if rb is not None and rb.is_reverse else (-1, 0)
    return (deliver(e, fi, left, e.left, bound, lives_on, analyzer),
            deliver(e, fi, right, e.right, bound, lives_on, analyzer))


def _unary(e: TpyExpr, bound: HandleUse, lives_on: bool,
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    ru = e.resolved_unaryop
    fi = ru.method if ru is not None else None
    return (deliver(e, fi, -1, e.operand, bound, lives_on, analyzer),)


_SEVERITY = {HandleUse.CONSUMED: 0, HandleUse.KEPT_BY_RESULT: 1,
             HandleUse.ESCAPES: 2}


def _longer(a: Hand, b: Hand) -> bool:
    """Whether hand `a` keeps its node longer than `b` does: an operand
    between two comparisons of a chain is handed on by both, and lives as
    the longer of the two says."""
    return ((_SEVERITY[a.bound], a.lives_on, a.held)
            > (_SEVERITY[b.bound], b.lives_on, b.held))


def _chained(e: TpyExpr, bound: HandleUse, lives_on: bool,
             analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # The comparisons sema built (`pairs`) are what is lowered, each the
    # chain's value as far as it goes, and their operands -- a comparator
    # coerced for its dunder included -- are each pair's.
    if not e.pairs:
        return _escape(e, bound, lives_on, analyzer)
    return tuple(Hand(pair, bound, lives_on, None) for pair in e.pairs)


def _subscript(e: TpyExpr, bound: HandleUse, lives_on: bool,
               analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    fi = e.dunders.get
    return (deliver(e, fi, -1, e.obj, bound, lives_on, analyzer),
            deliver(e, fi, 0, e.index, bound, lives_on, analyzer))


def _field(e: TpyExpr, bound: HandleUse, lives_on: bool,
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    if e.default_fill:
        # A parameter default's qualifier (`defs.K`): read where the default
        # is written, holding nothing of this statement.
        return (Hand(e.obj, HandleUse.CONSUMED, False, None),)
    if e.hidden_call is not None:
        # An access that dispatches to a method (a property setter,
        # `__getattr__` / `__setattr__` / `__delattr__`) is lowered as that
        # call.
        return (Hand(e.hidden_call, bound, lives_on, None, True),)
    return (deliver(e, e.resolved_property_getter, -1, e.obj, bound,
                     lives_on, analyzer),)


def _coerce(e: TpyExpr, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # A conversion to a value that cannot borrow (a view copied into an
    # owned `str`) consumes its operand; any other passes it on -- a view
    # handed on as a `str` no owning sink takes (a `str` parameter is a
    # view) included.
    if copied_out(e, analyzer) and not _view_handed_on(e, analyzer):
        return (Hand(e.expr, HandleUse.CONSUMED, False, None),)
    return (deliver(e, None, 0, e.expr, bound, lives_on,
                    analyzer)._replace(taker=None),)


def _view_handed_on(e: TpyCoerce, analyzer: 'ValueCategoryAnalyzer') -> bool:
    """A view converted to a type a binding views (`binds_buffer_view`: a
    `str` / `bytes`) where no owning sink's record copies it: the view
    itself is handed on."""
    t = analyzer.get_expr_type(e)
    src = analyzer.get_expr_type(e.expr)
    return (t is not None and src is not None and binds_buffer_view(t)
            and carries_borrow(src) and read_record(analyzer.ctx, e) is None)


def _ifexpr(e: TpyExpr, bound: HandleUse, lives_on: bool,
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    return (Hand(e.condition, HandleUse.CONSUMED, False, None),
            Hand(e.then_expr, bound, lives_on, None, True),
            Hand(e.else_expr, bound, lives_on, None, True))


def _comprehension(e: TpyExpr, bound: HandleUse, lives_on: bool,
                   analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # Its filters are consumed by its own loops, its elements live as its
    # result does, and a clause source is consumed unless the clause's
    # target carries what its elements point into into the result
    # (`_target_hands_out`), as a `for` target does out of its loop.
    return _clauses(e, bound, lives_on, analyzer, ())


def _clauses(e: TpyExpr, bound: HandleUse, lives_on: bool,
             analyzer: 'ValueCategoryAnalyzer',
             kept: 'tuple[object, ...]') -> 'tuple[Hand, ...]':
    """A comprehension as its nested loops (`for s in A: if c: for t in
    B(s): append(elt)`): its element lives as the result does, a filter is
    consumed, and each clause's source is consumed unless the clause's
    target is carried past its loop by what reads it later -- a later
    clause's source, a filter, the element -- the one loop-target rule
    (`_target_hands_out`)."""
    heads = comp_clause_children(e.generators)
    elements = [c for c in e.children() if not any(c is h for h in heads)]
    later: 'list[Hand]' = [_element(c, bound, lives_on, analyzer)
                           for c in elements]
    hands: 'list[Hand]' = list(later)
    for g in reversed(e.generators):
        filters = [Hand(c, HandleUse.CONSUMED, False, None)
                   for c in g.conditions]
        if g.head is not None:
            # An unpack clause takes its whole element apart each pass.
            filters.append(Hand(g.head.value, HandleUse.CONSUMED, False,
                                None))
        later.extend(filters)
        escaping, carried = _kept_reads(later, analyzer)
        targets = [(g.var, loop_bound_element(g, analyzer),
                    loop_target_refers(g, analyzer))]
        if g.head is not None:
            # An unpack clause's names, each as its member binds.
            targets += binding_target(g.head)[1]

        def hands_out(within: 'frozenset[str]') -> bool:
            return any(_target_hands_out(n, t, refers, within)
                       for n, t, refers in targets)

        if hands_out(escaping):
            # Carried out of the comprehension by a read that escapes.
            source: 'Hand' = Hand(g.iterable, HandleUse.ESCAPES, False, None,
                                  True)
        elif any(g.iterable is k for k in kept) or hands_out(carried):
            # Carried into the result: it lives as the result does.
            source = Hand(g.iterable, bound, lives_on, None, True)
        else:
            source = Hand(g.iterable, HandleUse.CONSUMED, False, None)
        later.append(source)
        hands.append(source)
        hands.extend(filters)
    return tuple(hands)


def _kept_reads(hands: 'list[Hand]', analyzer: 'ValueCategoryAnalyzer'
                ) -> 'tuple[frozenset[str], frozenset[str]]':
    """The names read under `hands` where their value escapes, and those
    where it is kept at all -- anything but consumed in the expression, a
    value that lives past it included."""
    escaping: set[str] = set()
    carried: set[str] = set()
    for hand in walk_hands(hands, analyzer):
        if isinstance(hand.node, TpyName):
            if hand.bound is HandleUse.ESCAPES:
                escaping.add(hand.node.name)
            if hand.bound is not HandleUse.CONSUMED or hand.lives_on:
                carried.add(hand.node.name)
    return frozenset(escaping), frozenset(carried)


def loop_element_type(source: TpyExpr, analyzer: 'ValueCategoryAnalyzer'
                      ) -> 'TpyType | None':
    """What iterating `source` binds its target to, an `Own` element kept
    as such (`get_iterable_element_type`)."""
    t = analyzer.get_expr_type(source)
    return (get_iterable_element_type(t, analyzer.registry, keep_own=True)
            if t is not None else None)


def _genexpr(e: TpyExpr, bound: HandleUse, lives_on: bool,
             analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    # A call of its generator function: the outermost source and the reads
    # of what it captures (`frame_captures`, held by reference) are that
    # frame's kept arguments; the rest is classified as a comprehension's,
    # its elements handed to whoever consumes the generator.
    return _clauses(e, bound, lives_on, analyzer,
                    (e.generators[0].iterable,)) + tuple(
        Hand(n, bound, lives_on, None, True) for n in e.frame_captures)


def _await(e: TpyExpr, bound: HandleUse, lives_on: bool,
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[Hand, ...]':
    if not awaited_result_keeps(analyzer.get_expr_type(e),
                                e.await_result_is_borrow):
        return (Hand(e.value, HandleUse.CONSUMED, False, None),)
    return (Hand(e.value, bound, lives_on, None, True),)


_LITERALS = (TpyArrayLiteral, TpySetLiteral, TpyDictLiteral, TpyTupleLiteral,
             TpyListRepeat, TpyVarargPack)

_BOUND_RULES: 'dict[type, Callable]' = {
    TpyName: _name,
    **dict.fromkeys((TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                     TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral,
                     TpyTypeParamConstruct), _leaf),
    **dict.fromkeys((TpyCall, TpyMethodCall, TpyCallLike), _call),
    TpyBinOp: _binop,
    TpyUnaryOp: _unary,
    TpyChainedCompare: _chained,
    TpySubscript: _subscript,
    TpySlice: _no_callee,
    TpyFieldAccess: _field,
    TpyFString: _no_callee,
    TpyCoerce: _coerce,
    **dict.fromkeys(_LITERALS, _pass),
    TpyIfExpr: _ifexpr,
    **dict.fromkeys((TpyListComprehension, TpySetComprehension,
                     TpyDictComprehension), _comprehension),
    TpyGeneratorExpression: _genexpr,
    TpyAwait: _await,
    # A `*` spread's operand is what its position takes: an argument's
    # contract, a literal's element.
    TpyStarUnpack: _pass,
    TpyNamedExpr: _walrus,
    TpyLambda: _lambda,
}


def binding_target(stmt: TpyStmt
                   ) -> 'tuple[TpyExpr, tuple[tuple, ...]] | None':
    """The value a binding statement binds and the names it binds it to,
    each with the type it holds (None: as the value is) and whether its
    binding refers to the value rather than holding one of its own: a
    declaration, an assignment to a name, a tuple target (each name one
    element, `unpack_member_refers`)."""
    if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
        return stmt.init, ((stmt.name, None, True),)
    if isinstance(stmt, TpyAssign) and isinstance(peel_coerce(stmt.target),
                                                  TpyName):
        return stmt.value, ((peel_coerce(stmt.target).name, None, True),)
    if isinstance(stmt, TpyTupleUnpack):
        types = stmt.target_types
        return stmt.value, tuple(
            (n, types[i] if i < len(types) else None,
             unpack_member_refers(stmt, i))
            for i, n in enumerate(stmt.targets) if n is not None)
    return None


def unpack_member_refers(stmt: TpyTupleUnpack, i: int) -> bool:
    """Whether tuple target `i` refers to its element, as sema stamped its
    binding: not where it takes the element over (`owns_target`) or copies
    it (`target_copies`); a reference binding (`is_ref`, `is_const_ref`)
    does; a value binding holds the element's value -- which for a
    reference type, or a tuple holding one by pointer, is still a borrow."""
    if stmt.owns_target(i) or _flag(stmt.target_copies, i):
        return False
    if _flag(stmt.is_ref, i) or _flag(stmt.is_const_ref, i):
        return True
    t = stmt.target_types[i] if i < len(stmt.target_types) else None
    return (t is None or not unwrap_qualifiers(t).is_value_type()
            or holds_reference_by_pointer(t))


def _flag(flags: 'list[bool]', i: int) -> bool:
    return i < len(flags) and flags[i]


def statement_hands(stmt: TpyStmt, escaping: 'frozenset[str]',
                    in_frame: bool, analyzer: 'ValueCategoryAnalyzer'
                    ) -> 'tuple[Hand, ...]':
    """THE statement classification: what statement `stmt` hands each of
    its expressions, one rule per statement kind (`_STMT_RULES`), given the
    names carried out of their binding (`escaping`). A discarded
    expression, a branch or loop condition, an `assert`, a `match` guard,
    what a `del` names and an assignment's target are consumed, and an
    augmented value is delivered to the operator; a loop's source, a
    `with` manager and a `match` subject are consumed unless a name the
    statement binds carries a borrow of it past the statement
    (`_target_hands_out`); a bound value (`binding_target`) is consumed by
    its names' own uses, past the statement, where none of them carries a
    borrow of it out (a resumable body only); a stored, returned, yielded
    or raised value escapes."""
    consumed = _STMT_RULES[type(stmt)](stmt, escaping, analyzer)
    bound = binding_target(stmt)
    lives_on = (in_frame and bound is not None and not any(
        _target_hands_out(n, t, refers, escaping)
        for n, t, refers in bound[1]))
    hands = []
    for e in stmt.exprs():
        if e is None:
            continue
        if any(e is c for c in consumed):
            hands.append(Hand(e, HandleUse.CONSUMED, False, None))
        elif bound is not None and e is bound[0] and lives_on:
            hands.append(Hand(e, HandleUse.CONSUMED, True, None))
        elif isinstance(stmt, TpyAugAssign) and e is stmt.value:
            hands.append(_aug_value(stmt, analyzer))
        elif copied_out(e, analyzer):
            # Stored, returned or yielded as its own copy.
            hands.append(Hand(e, HandleUse.CONSUMED, False, None))
        else:
            hands.append(Hand(e, HandleUse.ESCAPES, False, None))
    if isinstance(stmt, TpyAugAssign) and stmt.stored_value is not None:
        # The operation's result as the store receives it, through a
        # conversion over a stand-in for the operation (not a child).
        sv = stmt.stored_value
        hands.append(Hand(sv, HandleUse.CONSUMED if copied_out(sv, analyzer)
                          else HandleUse.ESCAPES, False, None))
    if in_frame and isinstance(stmt, (TpyReturn, TpyYield)):
        return tuple(h._replace(leaves=True) for h in hands)
    return tuple(hands)


def _aug_value(stmt: TpyAugAssign, analyzer: 'ValueCategoryAnalyzer'
               ) -> Hand:
    """An augmented assignment's value: an operand of the operator it
    resolved to, kept as long as the target only where the operator's
    result keeps it."""
    rb = stmt.resolved_inplace or stmt.resolved_binop
    fi = getattr(rb, "method", None) if rb is not None else None
    index = 0 if rb is None or not getattr(rb, "is_reverse", False) else -1
    return deliver(stmt.target, fi, index, stmt.value, HandleUse.ESCAPES,
                   False, analyzer)


def _none(stmt: TpyStmt, escaping: 'frozenset[str]',
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    return ()


def _all(stmt: TpyStmt, escaping: 'frozenset[str]',
          analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    return tuple(e for e in stmt.exprs() if e is not None)


def _for_each(stmt: TpyStmt, escaping: 'frozenset[str]',
               analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    return (() if loop_target_hands_out(stmt, escaping, analyzer)
            else (stmt.iterable,))


def _condition(stmt: TpyStmt, escaping: 'frozenset[str]',
                analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    return (stmt.condition,)


def _with(stmt: TpyStmt, escaping: 'frozenset[str]',
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    return tuple(item.context_expr for item in stmt.items
                 if not _target_hands_out(
                     item.target, item.enter_type,
                     element_borrow_form(item.enter_type, False), escaping))


def _match(stmt: TpyStmt, escaping: 'frozenset[str]',
            analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    guards = tuple(case.guard for case in stmt.cases
                   if case.guard is not None)
    captures = [c for case in stmt.cases
                for c in iter_capture_bindings(case.pattern)]
    if any(_target_hands_out(c.name, c.bound_type, element_borrow_form(
            c.bound_type, c.bind_by_value), escaping) for c in captures):
        return guards
    return (stmt.subject,) + guards


def _store(stmt: TpyStmt, escaping: 'frozenset[str]',
           analyzer: 'ValueCategoryAnalyzer') -> 'tuple[TpyExpr, ...]':
    # An assignment's target is the place written, not a value handed on.
    return (stmt.target,)


_STMT_RULES: 'dict[type, Callable]' = {
    TpyExprStmt: _all,
    TpyAssert: _all,
    TpyDelAttr: _all,
    TpyDelItem: _all,
    TpyForEach: _for_each,
    TpyIf: _condition,
    TpyWhile: _condition,
    TpyWith: _with,
    TpyMatch: _match,
    TpyAssign: _store,
    TpyAugAssign: _store,
    # Bound values are the binding's (`binding_target`); returned,
    # yielded and raised values escape; the rest has no expression.
    **dict.fromkeys((TpyVarDecl, TpyTupleUnpack, TpyReturn,
                     TpyYield, TpyRaise, TpyBreak, TpyContinue, TpyPassStmt,
                     TpyGlobal, TpyNonlocal, TpyImport, TpyDelVar,
                     TpyNestedDef, TpyTry), _none),
}


def _target_hands_out(name: 'str | None', t: 'TpyType | None',
                      refers: bool, escaping: 'frozenset[str]') -> bool:
    """Whether a name a statement binds is carried out of its binding
    (`escaping`) while it carries a borrow of what the statement built:
    the binding refers to the element (`refers`), or the value it holds
    may point -- except an object handed over into an `Own` slot, an
    owned value; one that keeps a fresh argument inside it is the
    record-keeps class (BUGS.md#record-keeps-fresh-argument-past-statement)."""
    if name is None or name not in escaping:
        return False
    bare = unwrap_readonly(unwrap_send_sync(t)) if t is not None else None
    if isinstance(bare, OwnType):
        # An owned value type is the value it holds (an `Own[StrView]` is
        # still a view).
        return bare.wrapped.is_value_type() and carries_borrow(bare.wrapped)
    return carries_borrow(t, borrow_form=refers)


def loop_target_hands_out(stmt: TpyForEach, escaping: 'frozenset[str]',
                          analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Whether a loop's target is carried out of its binding (`escaping`)
    while its elements carry a borrow -- into what the loop's source
    built, which then outlives the statement."""
    return _target_hands_out(
        stmt.var, loop_bound_element(stmt, analyzer),
        loop_target_refers(stmt, analyzer), escaping)


class IterStep(NamedTuple):
    """What an iteration binds its variable from, a `for` statement's or a
    comprehension clause's: the one place that states how a clause
    differs -- it never hoists its variable or consumes its source, its
    const is its own verdict (`const_loop_var`) and its element is the one
    its route resolved."""
    node: 'TpyForEach | TpyComprehensionGenerator'
    iterable: 'TpyExpr'
    # The element as the source yields it, and the one the variable binds
    # (an `Own` element the step does not hand over is its container's,
    # which the variable refers to).
    elem_type: 'TpyType | None'
    bound: 'TpyType | None'
    const: bool
    hoisted: bool
    # None: unknown before the body is lowered, the part the statement's
    # own facts decide (`for_consuming_shape`).
    consuming: 'bool | None'
    handed_over: bool
    copies: bool
    # The variable is the holder a head unpacks.
    unpack: bool


def iter_step(node: 'TpyForEach | TpyComprehensionGenerator',
              analyzer: 'ValueCategoryAnalyzer', *,
              elem: 'TpyType | None' = None) -> IterStep:
    """The step a `for` statement or a comprehension clause binds its
    variable from; `elem` is a clause's element as its route resolved
    it."""
    if isinstance(node, TpyForEach):
        et = (node.enum_iterable if node.enum_iterable is not None
              else resolved_loop_elem_type(node, analyzer))
        return IterStep(node, node.iterable, node.elem_type,
                        _bound_element(et, node), node.const_loop_var,
                        node.hoist_loop_var, None,
                        binds_handed_over_step(node), node.copies_var,
                        node.is_tuple_unpack)
    et = elem if elem is not None else loop_element_type(node.iterable,
                                                         analyzer)
    return IterStep(node, node.iterable, et, _bound_element(et, node),
                    bool(node.const_loop_var), False, False, False,
                    node.copies_var, node.unpack_vars is not None)


def _bound_element(et: 'TpyType | None',
                   node: 'TpyForEach | TpyComprehensionGenerator'
                   ) -> 'TpyType | None':
    if isinstance(et, OwnType) and not node.step_handover:
        return et.wrapped
    return et


def loop_bound_element(node: 'TpyForEach | TpyComprehensionGenerator',
                       analyzer: 'ValueCategoryAnalyzer', *,
                       elem: 'TpyType | None' = None) -> 'TpyType | None':
    """The element a `for` statement or a comprehension clause binds its
    variable to (`iter_step`)."""
    return iter_step(node, analyzer, elem=elem).bound


def loop_binding_of(node: 'TpyForEach | TpyComprehensionGenerator',
                    analyzer: 'ValueCategoryAnalyzer', *,
                    elem: 'TpyType | None' = None,
                    consuming: 'bool | None' = None
                    ) -> 'LoopBinding | None':
    """How a `for` statement or a comprehension clause binds its variable
    (`loop_binding_kind`), from its step (`iter_step`): the element, the
    const, hoist and copy verdicts and the consuming route -- `consuming`,
    which, unknown before the body is lowered, is the part the statement's
    own facts decide (`for_consuming_shape`)."""
    return step_binding(iter_step(node, analyzer, elem=elem), analyzer,
                        consuming=consuming)


def step_binding(step: IterStep, analyzer: 'ValueCategoryAnalyzer', *,
                 consuming: 'bool | None' = None) -> 'LoopBinding | None':
    """`loop_binding_of` over a step already built."""
    node = step.node
    if step.bound is None:
        return None
    if consuming is None:
        consuming = step.consuming
    if consuming is None:
        # Whether the body already declares the variable is a lowering
        # fact sema records nothing of: read as consuming, it refers.
        consuming = for_consuming_shape(node, analyzer)
    return loop_binding_kind(step.bound, step.const, hoisted=step.hoisted,
                             consuming=consuming, copies=step.copies,
                             handed_over=step.handed_over)


def loop_target_refers(node: 'TpyForEach | TpyComprehensionGenerator',
                       analyzer: 'ValueCategoryAnalyzer') -> bool:
    """Whether a loop's variable refers to the element rather than holding
    a value of its own: its binding (`loop_binding_of`) is a reference,
    or a value of a still-pending view. A hoisted slot's form is the
    planner's storage decision, read as the element's (`element_borrow_form`)."""
    kind = loop_binding_of(node, analyzer)
    et = loop_bound_element(node, analyzer)
    if kind is None or kind is LoopBinding.ASSIGN:
        return element_borrow_form(et, node.copies_var)
    if kind in (LoopBinding.OWNED, LoopBinding.OWNED_STORAGE):
        return False
    return kind in _REFERS or (
        et is not None and isinstance(peel_binding(et), PendingViewType))


_REFERS = frozenset((LoopBinding.REFERENCE, LoopBinding.CONST_REFERENCE,
                     LoopBinding.CONST_TYPED_REFERENCE))


def escaping_names(body: 'list[TpyStmt]',
                   analyzer: 'ValueCategoryAnalyzer') -> 'frozenset[str]':
    """`_escaping_once` to its fixed point: a loop whose target escapes
    hands its source on (`statement_hands`), which may carry a name read
    there out too."""
    names: 'frozenset[str]' = frozenset()
    while True:
        more = _escaping_once(body, analyzer, names)
        if more == names:
            return names
        names = more


def _escaping_once(body: 'list[TpyStmt]',
                   analyzer: 'ValueCategoryAnalyzer',
                   known: 'frozenset[str]') -> 'frozenset[str]':
    """The names a frame's await-lifted `body` carries a value out of the
    statement that bound it with: a read the bound rule says escapes (an
    argument a callee keeps, a container element, a return, a binding of
    another name), a capture of a lambda or a nested `def` (it outlives
    the statement). A rebind is no read; the draining uses --
    `await name`, `for ... in name` -- consume it where it stands."""
    out: set[str] = set(known)

    def body_walk(stmts: 'list[TpyStmt]') -> None:
        for s in stmts:
            if isinstance(s, TpyNestedDef):
                # What it captures (sema's `captured_names`), which lives as
                # long as the function the `def` binds.
                out.update(s.captured_names)
                continue
            for hand in walk_hands(statement_hands(s, known, True, analyzer),
                                   analyzer):
                if isinstance(hand.node, TpyName) and (
                        hand.bound is HandleUse.ESCAPES):
                    out.add(hand.node.name)
            for b in s.sub_bodies():
                body_walk(b)

    body_walk(body)
    return frozenset(out)

