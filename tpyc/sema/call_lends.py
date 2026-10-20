"""Call-result lending: which operands of a call its result may point into.

The storage walk (`storage_walk.py`) climbs an expression once; at each call
it climbs through -- a call end, a property getter step -- it asks a CALL
POLICY which operands the result may point into, and climbs on into each.
The policies are the one place that question is answered:

- MAY (`MayLend`): what the result may point into -- a borrowing
  constructor's first argument (`borrowing_ctor_arg`), the callee's recorded
  `return_borrows_from` (held-whole sources flagged), plus the receiver a
  call declares lending (`following_components`) when the climb asks for
  write access or a loan; a callee whose body is not analyzed yet answers
  its CONTRACT, every source flagged `assumed`; a body-less stub with no
  fact, its return convention; a callable value, nothing. A getter
  read is a method call whose instantiation decides whether it hands back a
  reference at all (`property_access_returns_cpp_ref`).
- MUST (`MustLend`): what the result's FORM proves outlives the statement
  (`proven_lend_roots`): a declared borrow stamped `ResultForm.BORROW`, or a
  known fact with a reference / borrowing-view / borrow-tuple result, every
  source itself proven; it refuses anything else.
- CONTRACT (`ContractLend`): what a callee may RETAIN under its declaration
  alone, order-free -- every operand less an `Own` slot and a pointer-free
  one, nothing for an `Own` or fresh result.
- VIEW (`ViewLend`): the operands a `str` / `bytes` view result may read.
- RETURN (`ReturnLend`) and TEMPORARY (`TempLend`): the durability and
  view-temporariness questions of the LIFETIME loop -- MAY / VIEW with
  overlays, a fresh result (`call_result_is_fresh`) a temporary.
- `NO_CALLS`: follows no call at all (the ephemeral generator-step root,
  the storage a `for` advances).

What the callee itself says -- a recorded fact, none yet, a stub's
convention, a callable value's contract, a fresh stamp -- is classified once
(`callee_lend_fact`); MAY, VIEW, TEMPORARY, RETURN and MUST map that one
value.

An `assumed` source is a guess a later fact may withdraw: a deferrable
verdict defers on it, a recorded fact and a hard verdict take proven sources
only, a loan on it is OPAQUE, a mutation credit widens.

The type helpers deciding whether a value may point into storage it does
not own (`may_hold_pointer` and its family) live here too: the policies and
the call-site source rule (`call_lend_sources`) read them.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Callable, NamedTuple

from ..parse import (
    ResultForm, TpyArrayLiteral, TpyAwait, TpyBinOp, TpyBytesLiteral,
    TpyCall, TpyCallLike, TpyDictLiteral, TpyExpr, TpyFieldAccess,
    TpyFString, TpyGeneratorExpression, TpyListRepeat, TpyMethodCall,
    TpyName, TpyNoneLiteral, TpySetLiteral, TpySlice, TpyStrLiteral,
    TpySubscript, TpyUnaryOp, TpyVarargPack, is_property_getter_read,
)
from ..type_def_registry import (
    is_borrowing_view_type, is_bytearray_type, is_bytes_type, is_enum_type,
    is_str_type, is_str_view_type, is_string_type, type_def_of,
)
from ..typesys import (
    ConcreteFrameType, FunctionInfo, LiteralType, NominalType, OptionalType,
    OwnType,
    PendingNumType, PendingViewType, PtrType,
    ReadonlyType, RecordInfo, TpyType, TupleType, UnionType,
    contains_type_param, declares_no_writes, held_whole_borrow_sources, is_bufferless_scalar,
    is_dyn_protocol, is_open_type_param_return, is_owned_leaf, is_protocol_type,
    recorded_return_borrow_sources, return_borrow_fact_pending,
    substitute_type_params_structural,
    unwrap_own, unwrap_qualifiers, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync, view_is_inherently_const,
)
from ..value_category import (
    CONTAINER_LITERAL_NODES, CallOperands, ExprTypeOf,
    ValueCategoryAnalyzer, call_operands,
    call_returns_cpp_ref, frame_factory_callee, getitem_call_of,
    lent_operand_binds_open_param,
    lent_operands, property_access_returns_cpp_ref,
)
from . import own_copy
from .storage_walk import (
    qualifier_kind,
    Ask, CallPolicy, LeafKind, StorageKey, Use, call_lends_receiver_at, operand_keys, storage_roots,
)
from ..storage_trace import Step, StepKind
from .type_ops import signature_may_return_borrow

if TYPE_CHECKING:
    from .context import SemanticContext


def escaping_borrow_tuple(slot: TpyType) -> 'tuple[TupleType, bool] | None':
    """The borrow-form tuple a return / yield slot hands out, peeling one
    Optional (`tuple[Box, Box] | None` lends exactly what its bare twin
    does), with whether the slot is readonly (provenance only, no write
    access). None when the slot carries no borrowing tuple."""
    ro = isinstance(slot, ReadonlyType)
    bare = unwrap_readonly(slot)
    if isinstance(bare, OptionalType):
        ro = ro or isinstance(bare.inner, ReadonlyType)
        bare = unwrap_readonly(bare.inner)
    if isinstance(bare, TupleType) and bare.has_borrowing_element():
        return bare, ro
    return None


def escaping_borrow_writes(slot: TpyType) -> bool:
    """Whether a borrow escaping through the return / yield `slot` grants
    whoever receives it write access to what it lends: a reference or a
    borrow-form tuple, neither readonly. A view, a readonly slot and a
    value grant none."""
    slot = unwrap_ref_type(unwrap_send_sync(slot))
    borrow_tuple = escaping_borrow_tuple(slot)
    if isinstance(slot, ReadonlyType):
        return False
    if borrow_tuple is not None:
        return not borrow_tuple[1]
    return not slot.is_value_type()


class Lent(Enum):
    """How a call lent the operand a projection climbed into (a CALL
    step's `how`, `LendSource.how`)."""
    # The callee's recorded `return_borrows_from`.
    RECORDED = "recorded"
    # A borrowing constructor (`borrowing_ctor_arg`).
    CONSTRUCTOR = "constructor"
    # A body-less stub's declared convention, or a receiver lent by the
    # call's declaration alone.
    CONVENTION = "convention"
    # The callee's contract: a pending callee, a callable value, a view
    # call with no fact.
    CONTRACT = "contract"
    # An awaited call's recorded sources.
    AWAITED = "awaited"
    # Every operand of an awaited call with no fact.
    AWAITED_ALL = "awaited_all"


class LendSource(NamedTuple):
    """One operand of a call -- or one lending element of a tuple-literal
    operand -- whose storage the call's result may point into."""
    # Parameter index; -1 is the receiver.
    idx: int
    expr: TpyExpr
    # The parameter, or the tuple element slot, `expr` fills.
    slot: TpyType | None
    # The result holds it only as a whole object (`held_whole_params`).
    held_whole: bool = False
    # A write through the callee's parameter reaches `expr`'s storage
    # (`lent_operands`); always True for the receiver.
    grants_write: bool = True
    # The callee DECLARES the position `expr` fills as an open type param:
    # its C++ slot is a mutable borrow whatever the body does.
    binds_open_param: bool = False
    # Not a lent operand: the storage a tuple temporary holds by value for
    # `expr` (`lent_operands`). It adds no root, but a result borrowing it
    # points into what dies with the statement.
    temp_backed: bool = False
    # A guess about a callee whose body is not analyzed yet (module
    # docstring): it may not lend this operand at all.
    assumed: bool = False
    # The receiver, lent only by the call's declaration: the body may reach
    # the result some other way (a `Ptr`, a global), so the loan's shape is
    # unknown.
    declared_only: bool = False
    # How the call lends it, for a projection that records it.
    how: Lent | None = None


def holds_no_pointer(t: TpyType) -> bool:
    """A value of this declared type holds nothing that can point into other
    storage: a bufferless scalar (a number whose width is still pending
    among them) or a value owning an opaque buffer (`is_owned_leaf`: `str`,
    `String`, `bytes`, `int`)."""
    t = unwrap_qualifiers(t)
    return (is_bufferless_scalar(t) or isinstance(t, PendingNumType)
            or is_owned_leaf(t))


def leaves_hold_no_pointer(t: TpyType) -> bool:
    """A binding of this declared type holds nothing that can point into
    other storage: no leaf -- through tuple, Optional and union members
    (`tuple[int32, int32]`, `str | None`) -- is a reference type, which a
    binding refers to rather than owns, or a value that may point
    (`value_may_point`)."""
    t = unwrap_qualifiers(t)
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return all(leaves_hold_no_pointer(m) for m in t.inner_types())
    return not (_is_reference_nominal(t) or value_may_point(t))


def _is_reference_nominal(t: TpyType) -> bool:
    # A generator or coroutine frame is no storage of its own: it keeps
    # what it borrows.
    return (isinstance(t, NominalType) and not t.is_value_type()
            and not isinstance(t, ConcreteFrameType)
            and not (is_protocol_type(t) or is_dyn_protocol(t)))


def value_may_point(t: TpyType, seen: frozenset[TpyType] = frozenset()) -> bool:
    """A value of this declared type (a field, a moved `self` field, a type
    argument) may be or hold a pointer into other storage. An allow-list:
    it holds none only when it is pointer-free (`holds_no_pointer`), a
    user value type whose type arguments, fields and bases hold none
    (`_parts_may_hold_pointer`), a reference type -- which holds its own
    storage; what its fields point at is `reference_may_hold_pointer`, and
    for a moved `self` field BUGS.md#self-aimed-ptr-field-borrow-unrecorded
    -- or an Optional / union / tuple of such members. Anything else (a
    `Ptr`, a view, a callable, a native value type, a generator or coroutine frame, a
    protocol, a type parameter, a type not settled yet) may point."""
    t = unwrap_qualifiers(t)
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return any(value_may_point(m, seen) for m in t.inner_types())
    if holds_no_pointer(t) or _is_reference_nominal(t):
        return False
    if isinstance(t, NominalType) and t.is_value_type():
        td = type_def_of(t)
        rec = td.record if td is not None else None
        # A native value type's fields do not describe what it holds.
        if (rec is not None and rec.is_value_type and not rec.is_native
                and not rec.is_borrowing_view):
            return _parts_may_hold_pointer(t, rec, seen)
    return True


def _parts_may_hold_pointer(t: NominalType, rec: RecordInfo | None,
                            seen: frozenset[TpyType]) -> bool:
    """Whether a record type may hold a pointer through a type argument
    (`W[StrView]`), a field or a base class (`may_hold_pointer` of each).
    A protocol it conforms to holds no storage of its own."""
    if t in seen:
        return False
    seen = seen | {t}
    args = [a for a in t.type_args if isinstance(a, TpyType)]
    if any(may_hold_pointer(a, seen) for a in args):
        return True
    if rec is None:
        return False
    subst = dict(zip(rec.type_params, args))
    return any(
        may_hold_pointer(
            substitute_type_params_structural(m, subst) if subst else m, seen)
        for m in [*(f.type for f in rec.fields),
                  *(p for p in rec.parents
                    if not (is_protocol_type(p) or is_dyn_protocol(p)))])


def reference_may_hold_pointer(t: NominalType,
                               seen: frozenset[TpyType] = frozenset()) -> bool:
    """Whether a reference-type value may hold a pointer into storage it
    does not own (`_parts_may_hold_pointer`)."""
    td = type_def_of(t)
    return _parts_may_hold_pointer(t, td.record if td is not None else None,
                                   seen)


def may_hold_pointer(t: TpyType, seen: frozenset[TpyType] = frozenset()) -> bool:
    """Whether a value of this type, a fresh one included, may be or hold a
    pointer into storage it does not own: `value_may_point` for a value
    type or a frame, `reference_may_hold_pointer` for a reference type,
    through tuple, Optional and union members."""
    t = unwrap_qualifiers(t)
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return any(may_hold_pointer(m, seen) for m in t.inner_types())
    if _is_reference_nominal(t):
        return reference_may_hold_pointer(t, seen)
    return value_may_point(t, seen)


def temp_element_can_back(slot: TpyType | None,
                          result: TpyType | None) -> bool:
    """Whether a call result of type `result` can point into the storage a
    tuple temporary holds by value at element slot `slot`.

    A reference-type result points at an object of its type, so only an
    element that is or holds a reference type can back it (a `str` or a
    number cannot hold a record) -- unless the result may itself hold a
    pointer (`reference_may_hold_pointer`: a view field, `W[StrView]`),
    which can point into any element. Any other result -- a view, a `Ptr`,
    an open type parameter -- may point into any storage, so it is kept."""
    if slot is None or result is None:
        return True
    r = unwrap_qualifiers(result)
    if isinstance(r, (OptionalType, UnionType, TupleType)):
        return any(temp_element_can_back(slot, m) for m in r.inner_types())
    if is_bufferless_scalar(r):
        return False
    if (r.is_value_type() or not isinstance(r, NominalType)
            or is_protocol_type(r) or is_dyn_protocol(r)):
        return True
    return (own_copy.contains_reference_type(slot)
            or reference_may_hold_pointer(r))



def call_lend_sources(ops: CallOperands,
                      indices: 'frozenset[int] | set[int] | None' = None,
                      held: 'frozenset[int]' = frozenset(), *,
                      expr_type: 'ExprTypeOf | None',
                      temp_backing: bool = False
                      ) -> list[LendSource]:
    """The sources behind the operand positions `indices` (None: every
    operand) of one call, in position order.

    THE call-site rule every provenance consumer of a call reads: a tuple
    literal at a tuple parameter lends each element whose slot borrows,
    exactly as that element would lend as a scalar argument at the
    element's slot, and an element whose slot owns lends nothing
    (`lent_operands`). A tuple literal BOUND to a local is a second rule
    (`_register_tuple_binding_borrows`: the element capture decides, a
    view element files no loan); the two share only the element iterator
    `tuple_literal_elems`. `expr_type`
    lets `lent_operands` see a `Ptr[T]` operand, which grants no write; a
    caller reading `grants_write` passes it, one that does not passes None.

    That answers "which caller storage does the result borrow". A consumer
    asking "can the result point into a temporary" passes `temp_backing`
    and also gets the owned elements of a tuple-literal operand, flagged
    `temp_backed` -- only those the result's type can point into
    (`temp_element_can_back`), and none when the call builds a frame,
    which holds the tuple by value for as long as the handle lives."""
    fi = ops.fi
    temp_backing = temp_backing and not (fi.is_async
                                         or frame_factory_callee(fi))
    result = fi.return_type
    out: list[LendSource] = []
    params = fi.params
    declared_params = fi.root.params
    for idx, arg in ops.indexed():
        if indices is not None and idx not in indices:
            continue
        if idx == -1:
            out.append(LendSource(-1, arg, None, -1 in held))
            continue
        slot = params[idx].type if 0 <= idx < len(params) else None
        declared = (declared_params[idx].type
                    if 0 <= idx < len(declared_params) else None)
        out.extend(LendSource(
            idx, lent.expr, lent.slot, idx in held, lent.grants_write,
            not lent.temp_backed
            and lent_operand_binds_open_param(lent, arg, declared),
            lent.temp_backed)
            for lent in lent_operands(arg, slot, expr_type=expr_type,
                                      temp_backing=temp_backing)
            if not lent.temp_backed
            or temp_element_can_back(lent.slot, result))
    return out




def borrowing_ctor_arg(call: TpyExpr) -> TpyExpr | None:
    """The operand a BORROWING CONSTRUCTOR lends its result: the first
    argument of the address-taking `Ptr[T](x)`, of a borrowing view's
    constructor (`StrView(s)`, `Span[T](xs)`) and of a `@value_ptr_coercion`
    callee (`take_ptr(x)`, whose `Ptr[T]` parameter takes `x`'s address).
    None for any other call -- `Ptr[T]()` included, which points nowhere."""
    if not isinstance(call, TpyCall) or not call.args:
        return None
    ct = call.call_type
    if ct is not None and (isinstance(ct, PtrType)
                           or is_borrowing_view_type(ct)):
        return call.args[0]
    fi = call.resolved_function_info
    if fi is not None and fi.value_ptr_coercion:
        return call.args[0]
    return None


def _ctor_lend(arg: TpyExpr,
              result: TpyType | None) -> LendSource:
    """The source a borrowing constructor (`borrowing_ctor_arg`) lends: its
    first argument, written through unless the result is const."""
    r = unwrap_qualifiers(result) if result is not None else None
    const = (r.is_readonly if isinstance(r, PtrType)
             else r is not None and view_is_inherently_const(r))
    return LendSource(0, arg, None, grants_write=not const,
                      how=Lent.CONSTRUCTOR)


def call_callee(call: TpyExpr) -> 'FunctionInfo | None':
    """The callee a call-shaped expression's borrow facts are read off
    (`call_operands`), or None for no resolved callee."""
    ops = call_operands(call)
    return None if ops is None else ops.fi


class CalleeFact(Enum):
    """What a call's callee says about which operands its result may point
    into (`callee_lend_fact`), most decisive first."""
    # A call-shaped expression with no resolved callee.
    UNRESOLVED = "unresolved"
    # A callable value: no body to read a fact off; its operands are its
    # contract.
    CALLABLE = "callable"
    # A callee whose body is not analyzed yet (`return_borrow_fact_pending`).
    PENDING = "pending"
    # A body-less stub recording no fact: its declared convention.
    STUB = "stub"
    # A recorded fact naming no source.
    KNOWN_EMPTY = "known_empty"
    # A recorded fact naming sources.
    RECORDED = "recorded"


class CalleeAccess(Enum):
    """How a call reaches its callee (`CalleeLend.access`)."""
    # Through a name (`f(s)`), a generator expression's frame call included.
    NAME = "name"
    # Read off a receiver (`h.f(s)`, `h.m(s)`), an operator dunder included.
    MEMBER = "member"
    # Through a callee expression's result (`make()(s)`, `f[T](s)`), a
    # borrowing constructor and the null `Ptr[T]()` excepted: no name or
    # receiver holds the callee.
    EXPRESSION = "expression"


class CalleeLend(NamedTuple):
    """A call's callee lend fact (`callee_lend_fact`)."""
    kind: CalleeFact
    # The call's operands; None only for UNRESOLVED.
    ops: CallOperands | None
    # The recorded sources (RECORDED); empty otherwise.
    sources: frozenset[int]
    # The callee DECLARES a borrowing-view result.
    declares_view: bool
    # Sema stamped this call's result fresh (`ResultForm.is_fresh`): it
    # borrows nothing. Only a call to a borrow-declared binding with
    # recorded sources is stamped, so the kind is then RECORDED.
    fresh: bool
    access: CalleeAccess


def _callee_access(call: TpyExpr) -> CalleeAccess:
    if _through_expression(call):
        return CalleeAccess.EXPRESSION
    if isinstance(call, (TpyCall, TpyGeneratorExpression)):
        return CalleeAccess.NAME
    return CalleeAccess.MEMBER


def callee_lend_fact(call: TpyExpr) -> CalleeLend:
    """THE classification of what a call's callee says about the operands
    its result may point into; every call policy maps this one value to its
    own answer rather than re-deciding it."""
    access = _callee_access(call)
    ops = call_operands(call)
    if ops is None:
        return CalleeLend(CalleeFact.UNRESOLVED, None, frozenset(), False,
                          False, access)
    fi = ops.fi
    rt = fi.return_type
    declares_view = rt is not None and is_borrowing_view_type(
        unwrap_readonly(rt))
    sources = recorded_return_borrow_sources(fi)
    if fi.is_callable_value:
        kind = CalleeFact.CALLABLE
    elif return_borrow_fact_pending(fi):
        kind = CalleeFact.PENDING
    elif fi.root.return_borrows_from is None:
        kind = CalleeFact.STUB
    else:
        kind = CalleeFact.RECORDED if sources else CalleeFact.KNOWN_EMPTY
    fresh = isinstance(call, TpyCallLike) and call.result_form.is_fresh
    return CalleeLend(kind, ops, sources, declares_view, fresh, access)


# The callee facts that record a `return_borrows_from`, empty or not.
_RECORDS_A_FACT = frozenset({CalleeFact.RECORDED, CalleeFact.KNOWN_EMPTY})
# No callee body to read a fact off: no callee, or a callable value.
_NO_CALLEE_BODY = frozenset({CalleeFact.UNRESOLVED, CalleeFact.CALLABLE})
# The callee facts a durability reading cannot trust: no callee body, or a
# body not analyzed yet.
_NO_DURABLE_FACT = _NO_CALLEE_BODY | {CalleeFact.PENDING}


def _view_names_operands(lend: CalleeLend) -> bool:
    """Whether a view-returning call's callee fact says which operands its
    view reads: recorded sources (a fresh stamp's included) or a callable
    value's contract through a name. Any other says nothing: the view is
    the callee's storage."""
    return lend.kind is CalleeFact.RECORDED or (
        lend.kind is CalleeFact.CALLABLE
        and lend.access is CalleeAccess.NAME)


def borrow_declared_sources(call: TpyCallLike) -> list[LendSource] | None:
    """The operands a call to a binding that DECLARES its result's borrow
    sources (`borrows=` / `element_of=`) lends by that declaration -- what
    `stamp_result_borrow` decides the call's result form from; None for a
    call whose result is already decided fresh."""
    lend = callee_lend_fact(call)
    if lend.kind is CalleeFact.UNRESOLVED or lend.fresh:
        return None
    return call_lend_sources(lend.ops, lend.sources, expr_type=None)


def _points_by_value(t: TpyType) -> bool:
    """A result handed back by value that still points into storage: a
    borrowing view or a borrow-form tuple."""
    return (is_borrowing_view_type(t)
            or escaping_borrow_tuple(t) is not None)


def _result_points(analyzer: ValueCategoryAnalyzer,
                   fi: FunctionInfo) -> bool:
    """Whether a call's result may point into existing storage, whether or
    not an operand lends it: a C++ reference (a constructor's is a fresh
    object), or a result pointing by value (`_points_by_value`)."""
    return not fi.is_constructor and (
        call_returns_cpp_ref(analyzer, fi)
        or _points_by_value(fi.return_type))


def _declared_own(t: TpyType | None) -> bool:
    return t is not None and isinstance(
        unwrap_readonly(unwrap_send_sync(t)), OwnType)


def _contract_sources(call: TpyExpr, lend: CalleeLend, type_of: ExprTypeOf,
                      expr_type: ExprTypeOf | None, *,
                      temp_backing: bool = False) -> list[LendSource]:
    """What a call's result MAY point into under its declared contract
    alone: every operand, the receiver included, less an `Own` slot (it
    moves its argument rather than lending it) and an operand whose type
    holds no pointer (`type_of`: it reaches the callee as a copy). Read
    from the signature, never from what the callee's body is proven to
    return, so the answer does not depend on which body was analyzed
    first. Nothing for a fresh result or one declared owned whole; every
    operand for a call no callee resolves. `lend` is the call's
    `callee_lend_fact`; `expr_type` and `temp_backing` are
    `call_lend_sources`'; a temp-backed element stays whatever its type, as
    the storage dying with the statement."""
    if lend.fresh:
        return []
    ops = lend.ops
    if ops is None:
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return []
        recv = [call.obj] if isinstance(call, TpyMethodCall) else []
        srcs = ([LendSource(-1, e, None) for e in recv]
                + [LendSource(i, a, None) for i, a in enumerate(call.args)]
                + [LendSource(-2, a, None)
                   for a in (call.kwargs or {}).values()])
    elif _declared_own(ops.fi.root.return_type):
        return []
    else:
        declared = ops.fi.root.params
        srcs = [src for src in call_lend_sources(ops, None,
                                                 expr_type=expr_type,
                                                 temp_backing=temp_backing)
                if not (0 <= src.idx < len(declared)
                        and _declared_own(declared[src.idx].type))]
    return [s for s in srcs
            if s.temp_backed or (t := type_of(s.expr)) is None
            or not holds_no_pointer(t)]


def _immutable_at(ctx: 'SemanticContext', expr: TpyExpr) -> bool:
    """An operand nothing can write through: one holding no pointer (a
    number, an owned `str`) or a view that is const by its type."""
    t = ctx.get_expr_type(expr)
    if t is None:
        return False
    t = unwrap_qualifiers(t)
    return (holds_no_pointer(t) or isinstance(t, PendingViewType)
            or view_is_inherently_const(t))


def _builds_frame(call: TpyExpr) -> bool:
    """Whether the call builds a generator or coroutine frame (a generator
    expression builds one through the call sema made for it)."""
    fi = call_callee(call)
    return fi is not None and (fi.is_async or frame_factory_callee(fi))


class MayLend(CallPolicy):
    """MAY: what a call's result may point into (module docstring). A
    LIFETIME climb takes the recorded sources only -- a receiver lent by
    declaration alone may hold its result's storage elsewhere (an
    `@auto_readonly` accessor reaching it through a `Ptr`); a WRITE, HANDLE
    or LOAN climb adds that receiver (`call_lends_receiver_at` at the
    component asked about), flagged `declared_only`. A call with a
    reference-shaped result that names no source points into storage that
    is neither fresh nor an operand's (a global, a `Ptr` target): unknown.
    The owned elements of a tuple-literal argument the result may point
    into come with the answer, flagged `temp_backed`: a loan on the result
    warns on them, a storage climb places nothing there."""
    follows_selects = True

    def __init__(self, ctx: 'SemanticContext') -> None:
        self.ctx = ctx

    def at_step(self, step: Step) -> bool:
        return True

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        srcs = self._sources(call, path, ask)
        if not srcs:
            return srcs
        # A generator or coroutine frame keeps what it borrows as its callee
        # declared it (a parameter its body does not write stays const):
        # writing through the frame -- advancing it -- is the body's own
        # writes, which the call edges settle. And an operand holding no
        # pointer (a `str`, a number) is immutable or copied: a result
        # viewing it (`p.split("/")`) writes nothing of it.
        frame = _builds_frame(call)
        return [s._replace(grants_write=False)
                if frame or _immutable_at(self.ctx, s.expr) else s
                for s in srcs]

    def _sources(self, call: TpyExpr, path: tuple,
                 ask: Ask) -> list[LendSource] | None:
        ctx = self.ctx
        getter = is_property_getter_read(call)
        rt = call.resolved_function_info.return_type if getter else None
        if getter and not (
                property_access_returns_cpp_ref(ctx, call)
                or _points_by_value(rt) or is_open_type_param_return(rt)):
            # Whether a getter read hands back a reference at all is decided
            # per INSTANTIATION (a bare type-param return) and per storage-ref
            # shape, which the body fact cannot see -- a view or a borrow
            # tuple points into storage by value, and inside a generic body
            # an open `T` may be a reference; WHERE it points is the method
            # rule below.
            return []
        lend = callee_lend_fact(call)
        kind = lend.kind
        if lend.fresh:
            return []
        ctor_arg = borrowing_ctor_arg(call)
        if ctor_arg is not None:
            return [_ctor_lend(ctor_arg, ctx.get_expr_type(call))]
        type_of = ctx.get_expr_type if ask is not Ask.LIFETIME else None
        if kind in _NO_CALLEE_BODY:
            # No resolved callee (a callable value): its contract. A callable
            # returns by value, so only a result that holds a pointer lends;
            # an open `T` is left out until instantiation-time facts exist
            # (`acc = func(acc, x)` in `functools.reduce` would keep `acc`).
            rt = ctx.get_expr_type(call)
            if (kind is CalleeFact.UNRESOLVED or rt is None
                    or contains_type_param(rt) or not may_hold_pointer(rt)):
                return []
            return [s._replace(how=Lent.CONTRACT)
                    for s in _contract_sources(call, lend, ctx.get_expr_type,
                                               type_of, temp_backing=True)]
        ops = lend.ops
        fi = ops.fi
        declared = (ops.obj is not None and ask is not Ask.LIFETIME
                    and call_lends_receiver_at(call, path))
        if kind is CalleeFact.PENDING:
            # Body not analyzed yet: its contract, every source a guess. A
            # guess files no temporary's loan, or a callee returning static
            # storage would warn in one definition order only
            # (BUGS.md#pending-callee-tuple-temporary-unchecked).
            srcs = ([s._replace(assumed=True, how=Lent.CONTRACT)
                     for s in _contract_sources(call, lend, ctx.get_expr_type,
                                                type_of)
                     if not (declared and s.idx == -1)]
                    if signature_may_return_borrow(fi) else [])
            if declared:
                srcs.insert(0, LendSource(-1, ops.obj, None,
                                          declared_only=True,
                                          how=Lent.CONVENTION))
            return srcs
        stub = kind is CalleeFact.STUB
        if stub:
            # A body-less stub records no fact; its declared convention is
            # the answer, and a reference it returns is into its receiver --
            # by declaration only, so the loan's shape is unknown.
            indices = (frozenset({-1}) if ops.obj is not None and (
                getter or call_returns_cpp_ref(ctx, fi)) else frozenset())
        else:
            indices = lend.sources
        only_declared = stub or (declared and -1 not in indices)
        if declared:
            indices = indices | {-1}
        srcs = call_lend_sources(ops, indices, held_whole_borrow_sources(fi),
                                 expr_type=type_of, temp_backing=True)
        if not srcs and (getter or _result_points(ctx, fi)):
            return None
        # A readonly method writes nothing through its receiver, so a write
        # through its result is not one it lends of the receiver -- unless the
        # declaration gives the result its receiver's access. Of an argument
        # only a callee that writes nothing BY DECLARATION says so
        # (`declares_no_writes`: `return iter(self.items)` keeps a method
        # const); a bodied callee's `is_readonly` speaks for its receiver.
        no_writes = declares_no_writes(fi)
        return [s._replace(declared_only=only_declared,
                           grants_write=declared or not fi.is_readonly,
                           how=(Lent.CONVENTION if only_declared
                                else Lent.RECORDED))
                if s.idx == -1
                else s._replace(grants_write=s.grants_write and not no_writes,
                                how=Lent.CONVENTION if stub
                                else Lent.RECORDED)
                for s in srcs]


def frame_call(call: TpyExpr) -> 'TpyCall | TpyMethodCall | None':
    """The call that builds a generator or coroutine frame, when `call` is
    one -- a generator expression builds its frame through the call sema
    made for it -- else None."""
    if isinstance(call, TpyGeneratorExpression):
        call = call.frame_creation
    return (call if isinstance(call, (TpyCall, TpyMethodCall))
            and _builds_frame(call) else None)


def frame_operands(call: TpyExpr) -> list[TpyExpr] | None:
    """The operands the generator or coroutine frame `call` builds keeps a
    reference to (`frame_call`): the receiver of a method call on an
    object, and the operands its callee's `return_borrows_from` names
    (every argument while that fact is unknown). None when `call` builds no
    frame. Each frame reader applies its own rule to these operands; MAY
    does not name a frame's receiver
    (BUGS.md#generator-method-receiver-borrow-unrepresented)."""
    frame = frame_call(call)
    if frame is None:
        return None
    fi = frame.resolved_function_info
    out: list[TpyExpr] = []
    if (isinstance(frame, TpyMethodCall) and not frame.is_static_call
            and not frame.user_module_call):
        out.append(frame.obj)
    lend = callee_lend_fact(frame)
    ops = CallOperands(fi, None, list(frame.args), frame.kwargs or {})
    out += [s.expr for s in call_lend_sources(
        ops, lend.sources if lend.kind in _RECORDS_A_FACT else None,
        expr_type=None)]
    return out


def frame_operand_keys(call: TpyExpr) -> list[StorageKey]:
    """The loan key of each operand the frame `call` builds keeps
    (`frame_operands`; a `*args` pack lends one per element), none when
    `call` builds no frame."""
    return [key for e in frame_operands(call) or ()
            for key in operand_keys(e)]


class ViewLend(CallPolicy):
    """VIEW: the operands a call's result may point into when that result
    is a view of a `str` / `bytes` buffer -- the BUFFER question: every
    operand but a bufferless scalar may own a buffer the view reads, where
    MAY asks whether an operand holds a POINTER (`holds_no_pointer`). A
    `Literal[str]` result is static storage and a non-view result a fresh
    value: no operands. A view result answers its recorded borrow sources,
    with the owned elements of a tuple-literal argument they hold
    (`temp_backed`); with no fact, a pending or known-empty one, a callable
    value or a fresh-stamped result, every argument and the typed receiver
    (a module or class qualifier lends nothing) that is no bufferless
    scalar. A view with none of those reads storage no operand names:
    unknown. Any other call shape (an operator dunder, a generator
    expression) is unknown.

    The LIFETIME climb (`view_leaves`) asks it also which nodes are leaves
    (`leaf_kind`, `call_kind`). It climbs paths, not a walrus or an await,
    nor a select below the value (the value's own select operands are its
    leaves)."""
    follows_selects = False

    def __init__(self, ctx: 'SemanticContext') -> None:
        self.ctx = ctx

    def at_step(self, step: Step) -> bool:
        return step.kind not in (StepKind.WALRUS, StepKind.AWAIT)

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        rt = self.ctx.get_expr_type(call)
        if rt is None or not is_borrowing_view_type(unwrap_readonly(rt)):
            return []
        lend = callee_lend_fact(call)
        if lend.kind is CalleeFact.RECORDED and not lend.fresh:
            return [s._replace(how=Lent.RECORDED)
                    for s in call_lend_sources(lend.ops, lend.sources,
                                               expr_type=None,
                                               temp_backing=True)
                    ] or None
        operands = [LendSource(i, a, None, how=Lent.CONTRACT)
                    for i, a in enumerate(call.args)]
        operands += [LendSource(-2, a, None, how=Lent.CONTRACT)
                     for a in (call.kwargs or {}).values()]
        if (isinstance(call, TpyMethodCall)
                and self.ctx.get_expr_type(call.obj) is not None):
            operands.append(LendSource(-1, call.obj, None,
                                       how=Lent.CONTRACT))
        out = []
        for src in operands:
            at = self.ctx.get_expr_type(src.expr)
            if not is_bufferless_scalar(
                    unwrap_readonly(unwrap_ref_type(at))
                    if at is not None else None):
                out.append(src)
        return out or None

    def leaf_kind(self, node: TpyExpr,
                  above: Step | None) -> LeafKind | None:
        """What `node` is when the view climb must stop at it, asked before
        it steps past the node (`above`: the step the climb took onto it):
        STATIC storage (a literal, an enum member's `.name`), a CONSTANT or
        a GLOBAL read off a module or class qualifier, a TEMPORARY (a
        `bytes` literal's slice builds owned bytes); None to climb on."""
        if isinstance(node, TpyBytesLiteral) and above is not None and (
                above.kind is StepKind.INDEX):
            return LeafKind.TEMPORARY
        # An operator is no leaf here: the climb reaches it as an
        # unanswered call or an unplaceable end.
        if shared_leaf_kind(node, self.ctx) is LeafKind.STATIC:
            return LeafKind.STATIC
        return qualifier_read_kind(node, self.ctx)

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> LeafKind:
        """What the result of `call` is as a view's leaf when it lends no
        operand (`at_call` answered `srcs`, None or empty): a `Literal[str]`
        result is STATIC and any other fresh one a TEMPORARY; a view call
        that may read no operand is UNNAMED, any other call shape
        UNPLACEABLE."""
        if srcs is not None:
            return _fresh_kind(self.ctx, call)
        return (LeafKind.UNNAMED
                if isinstance(call, (TpyCall, TpyMethodCall))
                else LeafKind.UNPLACEABLE)


def _fresh_kind(ctx: 'SemanticContext', call: TpyExpr) -> LeafKind:
    """A fresh call result as a leaf: STATIC for a `Literal[str]` value,
    else a TEMPORARY."""
    rt = ctx.get_expr_type(call)
    return (LeafKind.STATIC if isinstance(rt, LiteralType)
            and rt.is_str_base() else LeafKind.TEMPORARY)


def shared_leaf_kind(node: TpyExpr,
                     ctx: 'SemanticContext') -> LeafKind | None:
    """The leaf rules the LIFETIME policies share: STATIC storage for a
    `str` / `bytes` literal and an enum member's `.name`, a TEMPORARY for
    every operator -- an and/or of two C++ forms builds a common-type
    value, a resolved dunder's body may be analyzed later. None for any
    other node. A sliced `bytes` literal is each policy's own question
    (BUGS.md#bytes-literal-slice-owned-temporary)."""
    if (isinstance(node, (TpyStrLiteral, TpyBytesLiteral))
            or is_enum_name_read(node, ctx)):
        return LeafKind.STATIC
    if isinstance(node, (TpyBinOp, TpyUnaryOp)):
        return LeafKind.TEMPORARY
    return None


def qualifier_read_kind(node: TpyExpr,
                        ctx: 'SemanticContext') -> LeafKind | None:
    """A read off a module or class qualifier (`mod.X`, `Cls.X`): a
    CONSTANT for a view, else a GLOBAL, neither one durable storage -- a
    plain module variable can be rebound through `global` in its own
    module. None for any other node. (A read whose stamped referent is
    static storage is the climb's own leaf, `static_read_kind`.)"""
    type_of = ctx.get_expr_type
    if not (isinstance(node, TpyFieldAccess) and isinstance(node.obj, TpyName)
            and type_of(node.obj) is None):
        return None
    return qualifier_kind(node, ctx)


def is_enum_name_read(expr: TpyExpr, ctx: 'SemanticContext') -> bool:
    """True for an enum member's `.name`.

    Its value is a view of EnumUtil's STATIC member-name table, not of the
    receiver (sema types it STRVIEW in `sema/expressions.py`), so it has the
    static lifetime a `str` literal has. The fact belongs to the FIELD, not
    to the receiver: `.value` off the same receiver is the underlying value
    and borrows nothing static.

    The read's own STRVIEW type is part of the question, not a caller's
    precondition: the claim being made is about a view of that table, so a
    read typed anything else cannot be answered True here and leave a
    caller that forgot the guard asserting static lifetime for it.
    """
    return (isinstance(expr, TpyFieldAccess)
            and expr.field == "name"
            and is_str_view_type(ctx.get_expr_type(expr))
            and is_enum_type(ctx.get_expr_type(expr.obj)))


def call_result_is_fresh(ctx: 'SemanticContext', call: TpyExpr, *,
                         proof: bool = False) -> bool:
    """Whether the call-shaped `call` hands back a FRESH object, which a
    borrow of it dies with: a fresh-stamped result (`ResultForm.is_fresh`),
    a constructor (a borrowing one excepted: `borrowing_ctor_arg`; a
    `Ptr[T]()` points nowhere), an `Own[T]` return, an owned `str` /
    `String` / `bytes` / `bytearray` result, or a reference-type result the
    callee's convention returns by value -- a `Callable` returning a record
    included.

    With `proof`, the answer must prove the result fresh: an owned buffer
    is not asked about, and a callable value counts only through a
    declared `Own[T]` -- its signature is the `Fn` type's, which says
    nothing else about ownership."""
    lend = callee_lend_fact(call)
    if lend.fresh:
        return True
    if isinstance(call, TpyCall) and call.call_type is not None:
        ct = call.call_type
        if isinstance(ct, PtrType):
            return False
        if is_borrowing_view_type(ct):
            return not proof and not call.args
        return True
    if (isinstance(call, TpyCall) and isinstance(call.func, TpyName)
            and call.func_name in ctx.registry.records):
        return True
    if lend.kind is CalleeFact.UNRESOLVED:
        return False
    fi = lend.ops.fi
    callable_value = lend.kind is CalleeFact.CALLABLE
    if fi.is_constructor:
        return True
    if (isinstance(call, TpyCallLike)
            and call.result_form is not ResultForm.NOT_DECLARED):
        return False
    ret = unwrap_readonly(fi.return_type) if fi.return_type else None
    if isinstance(ret, OwnType):
        return True
    if not proof and (
            is_str_type(fi.return_type) or is_string_type(fi.return_type)
            or is_bytes_type(fi.return_type)
            or is_bytearray_type(fi.return_type)
            or is_bytearray_type(unwrap_own(unwrap_readonly(
                ctx.get_expr_type(call))))):
        return True
    if ((proof and callable_value) or ret is None
            or is_open_type_param_return(fi.root.return_type)):
        return False
    # A callable value returns by value whatever its result type: no C++
    # reference crosses a `Callable`.
    return (isinstance(ret, NominalType) and not ret.is_value_type()
            and not (is_protocol_type(ret) or is_dyn_protocol(ret))
            and (callable_value or not call_returns_cpp_ref(ctx, fi)))


def _is_null_ptr(call: TpyExpr) -> bool:
    return (isinstance(call, TpyCall) and isinstance(call.call_type, PtrType)
            and not call.args)


def _through_expression(call: TpyExpr) -> bool:
    """A call through a callee EXPRESSION (`make()(s)`): no name to read a
    borrow fact off."""
    return (isinstance(call, TpyCall) and not isinstance(call.func, TpyName)
            and borrowing_ctor_arg(call) is None and not _is_null_ptr(call))


def _recorded_index_missing(call: TpyExpr, lend: CalleeLend) -> bool:
    """Whether the callee's recorded `return_borrows_from` (`lend`, the
    call's `callee_lend_fact`) names a position this call fills with no
    operand: the result's provenance is then unknown."""
    if (not isinstance(call, (TpyCall, TpyMethodCall))
            or lend.kind is not CalleeFact.RECORDED):
        return False
    present = {idx for idx, _ in lend.ops.indexed()}
    return not lend.sources <= present


def _builtin_own_result(ctx: 'SemanticContext', call: TpyExpr) -> bool:
    """An `Own[T]` result of a callee that is no user function (a free
    call) or of a method on no user record: the durability readers do not
    call it a temporary (BUGS.md#user-own-factory-exempt-as-builtin)."""
    # Returned at an open `-> T` it copies at a value T and fails the C++
    # build at a record T (BUGS.md#generic-return-val-or-ref-rvalue).
    if isinstance(call, TpyCallLike) and call.result_form.is_fresh:
        return False
    if not isinstance(call, (TpyCall, TpyMethodCall)):
        return False
    fi = call.resolved_function_info
    if fi is None or not isinstance(fi.return_type, OwnType):
        return False
    if isinstance(call, TpyMethodCall):
        recv = ctx.get_expr_type(call.obj)
        return not (isinstance(recv, NominalType) and recv.is_user_record)
    return (call.call_type is None and isinstance(call.func, TpyName)
            and call.func_name not in ctx.registry.records
            and ctx.registry.get_function(call.func_name) is None
            and not is_bytearray_type(unwrap_own(unwrap_readonly(
                ctx.get_expr_type(call)))))


_RETURN_TEMPORARIES = (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                       TpyFString, TpyListRepeat)


class ReturnLend(CallPolicy):
    """RETURN: where the storage a returned or yielded borrow points into
    lives -- MAY at LIFETIME (`MayLend`), with the durability overlays:

    - a fresh result (`call_result_is_fresh`) is a TEMPORARY before MAY is
      asked -- an `Own[T]` result of a callee no user function or record
      method answers excepted (`_builtin_own_result`) -- a `Ptr[T]()` NULL;
    - a call through a callee expression (`CalleeAccess.EXPRESSION`) is a
      TEMPORARY, a callable value through a name or read off a receiver
      UNNAMED (its contract is not read: following it drops the view rule
      and admits a copied local);
    - a callee whose body is not analyzed yet (`return_borrow_fact_pending`)
      is UNNAMED, and so is a call MAY names no source of;
    - a borrow-returning await lends the awaited call's recorded sources,
      else every operand (`at_await`).

    A literal, an enum member's `.name` and the shapes that build a fresh
    value (a container literal, an f-string, a list repeat, a non-borrow
    await, a slice of an owning container, every operator -- and/or and a
    resolved dunder included: an and/or of two C++ forms builds a
    temporary) are classified before the climb; a node of a
    borrowing-view type no rule classifies is a VIEWNODE. A ternary and a
    `*args` pack below the value are followed per operand.

    `strict` is the closed-world reading (a recorded source naming no
    operand of the call is UNNAMED). `proof` is the reading that must PROVE
    a temporary (`borrow_temp_root`): a fresh result only by
    `call_result_is_fresh`'s proof, a call only through its recorded
    sources, no path through a hidden call, a walrus or an await, and the
    and/or arms followed."""
    follows_selects = True

    def __init__(self, ctx: 'SemanticContext', *, strict: bool = False,
                 proof: bool = False) -> None:
        self.ctx = ctx
        self.strict = strict
        self.proof = proof
        self.may = MayLend(ctx)

    def at_step(self, step: Step) -> bool:
        if not self.proof:
            return True
        node = step.node
        if step.kind is StepKind.FIELD:
            return node.hidden_call is None
        if step.kind is StepKind.INDEX:
            return (node.slice_function_info is None
                    and getitem_call_of(node) is None)
        return step.kind is StepKind.COERCE

    def leaf_kind(self, node: TpyExpr,
                  above: Step | None) -> LeafKind | None:
        if self.proof:
            if (isinstance(node, CONTAINER_LITERAL_NODES)
                    and not isinstance(node, TpyGeneratorExpression)):
                return LeafKind.TEMPORARY
            return None
        kind = shared_leaf_kind(node, self.ctx)
        if kind is not None:
            return kind
        if isinstance(node, TpyNoneLiteral):
            return LeafKind.STATIC
        if isinstance(node, _RETURN_TEMPORARIES) or (
                isinstance(node, TpyAwait)
                and not node.await_result_is_borrow):
            return LeafKind.TEMPORARY
        if isinstance(node, TpySubscript) and isinstance(node.index,
                                                        TpySlice):
            # A slice of an owning container builds a fresh one; only a
            # view result (a `str` or Span slice) points into its object.
            t = self.ctx.get_expr_type(node)
            if t is None or not is_borrowing_view_type(
                    unwrap_readonly(unwrap_ref_type(t))):
                return LeafKind.TEMPORARY
        if isinstance(node, TpyGeneratorExpression):
            return LeafKind.UNPLACEABLE
        return None

    def end_kind(self, node: TpyExpr) -> LeafKind:
        if self.proof:
            return LeafKind.UNPLACEABLE
        # Fail closed: a node of a borrowing-view type no rule classifies
        # has unknown provenance.
        t = self.ctx.get_expr_type(node)
        return (LeafKind.VIEWNODE if t is not None
                and is_borrowing_view_type(unwrap_readonly(t))
                else LeafKind.UNPLACEABLE)

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        if ((self.proof or not _builtin_own_result(self.ctx, call))
                and call_result_is_fresh(self.ctx, call, proof=self.proof)):
            return []
        if _is_null_ptr(call):
            return []
        if borrowing_ctor_arg(call) is not None:
            return None if self.proof else self.may.at_call(call, path, ask)
        lend = callee_lend_fact(call)
        if (lend.access is CalleeAccess.EXPRESSION
                or lend.kind in _NO_DURABLE_FACT):
            return None
        srcs = self.may.at_call(call, path, ask)
        if not srcs or any(s.assumed for s in srcs):
            return None
        if self.proof and any(s.how is not Lent.RECORDED for s in srcs):
            return None
        if self.strict and _recorded_index_missing(call, lend):
            return None
        return srcs

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> LeafKind:
        if srcs is not None:
            return (LeafKind.NULL if _is_null_ptr(call)
                    else LeafKind.TEMPORARY)
        if (not self.proof and callee_lend_fact(call).access
                is CalleeAccess.EXPRESSION):
            return LeafKind.TEMPORARY
        return LeafKind.UNNAMED

    def at_await(self, step: Step) -> 'list[LendSource] | LeafKind':
        if self.proof:
            return LeafKind.UNPLACEABLE
        op = step.node.value
        if not isinstance(op, (TpyCall, TpyMethodCall)):
            return LeafKind.TEMPORARY
        lend = callee_lend_fact(op)
        obj = op.obj if isinstance(op, TpyMethodCall) else None
        if lend.kind is CalleeFact.RECORDED:
            if self.strict and _recorded_index_missing(op, lend):
                return LeafKind.UNNAMED
            return [s._replace(how=Lent.AWAITED) for s in call_lend_sources(
                lend.ops, lend.sources, expr_type=None, temp_backing=True)]
        if self.strict:
            return LeafKind.UNNAMED
        # Fail closed on a borrow-await with no fact: every operand.
        return [LendSource(-1 if e is obj else i, e, None,
                           how=Lent.AWAITED_ALL)
                for i, e in enumerate(([obj] if obj is not None else [])
                                      + list(op.args))]


class TempLend(CallPolicy):
    """TEMPORARY: whether a view bound to the value reads storage that dies
    at the end of the statement -- a binding question: a local or a field
    outlives a same-scope binding. A borrowing constructor lends its
    argument; a view result answers VIEW (`ViewLend`; a view's constructor
    with no argument is a TEMPORARY); any other fresh result
    (`call_result_is_fresh`) or one holding no pointer is a TEMPORARY (a
    `Literal[str]` one STATIC), a reference result MAY (`MayLend`). A
    callee whose body is not analyzed yet is trusted by its result: a
    view-returning free call is UNNAMED, a view method lends its receiver,
    any other result is a TEMPORARY. A view call VIEW is asked about only
    when its callee fact names its operands (`_view_names_operands`); any
    other view call is trusted the same way. Literals and an enum member's
    `.name` are STATIC, a module or class qualifier's read a CONSTANT or a
    GLOBAL, every operator (and/or included) a TEMPORARY; it climbs fields,
    ternary selects and walruses, not an await or a `*args` pack."""
    follows_selects = True

    def __init__(self, ctx: 'SemanticContext') -> None:
        self.ctx = ctx
        self.view = ViewLend(ctx)
        self.may = MayLend(ctx)

    def at_step(self, step: Step) -> bool:
        return step.kind is not StepKind.AWAIT

    def leaf_kind(self, node: TpyExpr,
                  above: Step | None) -> LeafKind | None:
        kind = shared_leaf_kind(node, self.ctx)
        if kind is not None:
            return kind
        if isinstance(node, (TpyGeneratorExpression, TpyVarargPack)):
            return LeafKind.UNPLACEABLE
        return qualifier_read_kind(node, self.ctx)

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        ctx = self.ctx
        arg = borrowing_ctor_arg(call)
        if arg is not None:
            return [_ctor_lend(arg, ctx.get_expr_type(call))]
        rt = ctx.get_expr_type(call)
        view = rt is not None and is_borrowing_view_type(unwrap_readonly(rt))
        if view and isinstance(call, TpyCall) and call.call_type is not None:
            # A view's constructor with no argument views nothing that
            # outlives it.
            return []
        lend = callee_lend_fact(call)
        if lend.kind is CalleeFact.PENDING or (
                view and not _view_names_operands(lend)):
            # Trusted by the callee's DECLARED result: a type parameter
            # instantiated at a view (`dict.pop` -> V) hands back the
            # argument it was given, not storage the callee owns.
            if not (view and lend.declares_view):
                return []
            if isinstance(call, TpyMethodCall):
                return [LendSource(-1, call.obj, None, how=Lent.CONTRACT)]
            return None
        if view:
            return self.view.at_call(call, path, ask)
        if call_result_is_fresh(ctx, call) or (
                rt is not None and holds_no_pointer(rt)):
            return []
        return self.may.at_call(call, path, ask)

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> LeafKind:
        if srcs is not None:
            return _fresh_kind(self.ctx, call)
        return LeafKind.UNNAMED


class EveryTempLend(CallPolicy):
    """EVERY: each temporary a borrow-form value MAY point into -- the
    reading that must name every one rather than prove one
    (`TypeCompatibility.borrow_temp_roots`). A fresh value is a TEMPORARY
    (a container literal, a constructor of no view or `Ptr`, a fresh-stamped
    result, `call_result_is_fresh`'s proof); any other call lends each
    operand it does not take by ownership, unless its recorded
    `return_borrows_from` names its sources (a temp-backed tuple element
    among them). A subscript through a dunder or a slice callee is refused
    (UNPLACEABLE): the reader climbs its object and its index itself, as it
    climbs the operands of any other node the climb ends at.

    `stop` answers for a node the caller decides itself (its temporaries,
    gathered in `found`; None climbs on), and `top` with `through` is the
    value asked about, whose own freshness is not the question: what it
    points into is."""
    follows_selects = True

    def __init__(self, ctx: 'SemanticContext', top: TpyExpr, *,
                 stop: 'Callable[[TpyExpr], list | None] | None' = None,
                 through: bool = False) -> None:
        self.ctx = ctx
        self.top = top
        self.stop = stop
        self.through = through
        self.found: list = []
        self._fresh: list[TpyExpr] = []

    def _is_top(self, node: TpyExpr) -> bool:
        return self.through and node is self.top

    def _stopped(self, node: TpyExpr) -> bool:
        if self.stop is None or self._is_top(node):
            return False
        found = self.stop(node)
        if found is None:
            return False
        self.found += found
        return True

    def at_step(self, step: Step) -> bool:
        node = step.node
        if step.kind is StepKind.INDEX:
            return (node.slice_function_info is None
                    and getitem_call_of(node) is None)
        return True

    def leaf_kind(self, node: TpyExpr,
                  above: Step | None) -> LeafKind | None:
        if self._stopped(node):
            return LeafKind.STATIC
        if isinstance(node, TpyFieldAccess) and node.names_type is not None:
            return LeafKind.STATIC
        if (isinstance(node, CONTAINER_LITERAL_NODES)
                and not isinstance(node, TpyGeneratorExpression)
                and not self._is_top(node)):
            return LeafKind.TEMPORARY
        return None

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        # A getter is a step, which the classifier hook is not asked about.
        if is_property_getter_read(call) and self._stopped(call):
            return []
        lent = [LendSource(i, e, None) for i, e in every_lent_operand(call)]

        def fresh() -> list[LendSource]:
            if self._is_top(call):
                return lent
            self._fresh.append(call)
            return []

        if isinstance(call, TpyCall) and call.call_type is not None:
            if (isinstance(call.call_type, PtrType)
                    or is_borrowing_view_type(call.call_type)):
                return lent
            return fresh()
        if isinstance(call, TpyCallLike) and call.result_form.is_fresh:
            return fresh()
        ops = call_operands(call)
        if ops is None:
            return lent
        if call_result_is_fresh(self.ctx, call, proof=True):
            return fresh()
        if ops.fi.root.return_borrows_from is None:
            return lent
        return call_lend_sources(ops, recorded_return_borrow_sources(ops.fi),
                                 expr_type=None, temp_backing=True)

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> LeafKind:
        return (LeafKind.TEMPORARY if any(c is call for c in self._fresh)
                else LeafKind.STATIC)


def every_lent_operand(call: TpyExpr) -> list[tuple[int, TpyExpr]]:
    """Every operand of `call` with its source index, less one an
    ownership-taking slot moves into the result (it is the result's own
    storage); every child, at -2, for a node no callee resolves."""
    ops = call_operands(call)
    if ops is None:
        return [(-2, e) for e in call.children()]
    return [(i, e) for i, e in ops.indexed() if not ops.takes_ownership(i)]


class MustLend(CallPolicy):
    """MUST: the operands a call's result points into when its FORM proves
    that storage outlives the statement (`proven_lend_roots`): a call sema
    stamped a borrow (`ResultForm.BORROW`), or a call whose borrow facts are
    known and whose result is a C++ reference, a borrowing view or a
    borrow-form tuple. It climbs a plain field read, a container subscript
    and a coercion that renders its inner through, and refuses everything
    else: a select, a walrus, an await, a getter, a `__getattr__` hop, a
    subscript through a dunder or an Optional check, a fresh-building
    coercion, a constructor, a callee whose facts are unknown."""
    follows_selects = False

    def __init__(self, analyzer: ValueCategoryAnalyzer) -> None:
        self.ctx = analyzer

    def at_step(self, step: Step) -> bool:
        node = step.node
        if step.kind is StepKind.COERCE:
            return not node.coercion.builds_fresh_value
        if step.kind is StepKind.FIELD:
            return node.hidden_call is None
        if step.kind is StepKind.INDEX:
            return not (getitem_call_of(node) is not None
                        or node.slice_function_info is not None
                        or getattr(node, "needs_optional_runtime_check",
                                   False))
        return False

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        if is_property_getter_read(call):
            return None
        lend = callee_lend_fact(call)
        if (isinstance(call, TpyCallLike)
                and call.result_form is not ResultForm.NOT_DECLARED):
            if call.result_form is not ResultForm.BORROW:
                return None
        elif not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        elif (lend.kind not in _RECORDS_A_FACT
                or not _result_points(self.ctx, lend.ops.fi)):
            return None
        if lend.kind is CalleeFact.UNRESOLVED:
            return None
        return call_lend_sources(lend.ops, lend.sources, expr_type=None)


class ContractLend(CallPolicy):
    """CONTRACT: what a callee may RETAIN of its operands under its
    declaration alone (`_contract_sources`), order-free: a callee may keep
    an argument elsewhere (`k.save(t[0])` storing a `Ptr` to it), whatever
    its result. A pointer-free operand reaches the callee as a copy, so it
    keeps nothing of it. A generator expression keeps its source and the
    captures its yield may point into; a capture it holds whole is the
    creator's variable, read afresh at each pull. An operator dunder is not
    answered (unknown)."""
    follows_selects = True

    def __init__(self, ctx: ValueCategoryAnalyzer) -> None:
        self.ctx = ctx
        self.type_of = ctx.get_expr_type

    def at_step(self, step: Step) -> bool:
        return True

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        if isinstance(call, TpyGeneratorExpression):
            lend = callee_lend_fact(call)
            if lend.kind is CalleeFact.UNRESOLVED:
                return None
            return [s for s in call_lend_sources(
                        lend.ops, None, held_whole_borrow_sources(lend.ops.fi),
                        expr_type=None)
                    if not (s.held_whole and s.idx >= 0)]
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        return _contract_sources(call, callee_lend_fact(call), self.type_of,
                                 None)


class NoCalls(CallPolicy):
    """Follows no call, nor an await; it climbs paths, walruses and
    selects. For the questions a call's lends must not answer: the
    generator step a value reads from (whether a lending call keeps the
    step alive is undecided), and the storage a `for` advances (a call's
    result is a fresh iterator the loop advances in place of what it
    borrows)."""
    follows_selects = True

    def at_step(self, step: Step) -> bool:
        return step.kind is not StepKind.AWAIT

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        return None


NO_CALLS = NoCalls()


def proven_lend_roots(analyzer: ValueCategoryAnalyzer,
                      expr: TpyExpr) -> list[str] | None:
    """The names whose storage `expr` hands out a reference into, when its
    FORM proves that storage outlives the statement it is read in (the
    MUST policy, `MustLend`); None when it does not. The one root set the
    lending verdict, the loans a borrow-declared result files and the
    mutable use it makes of its operands all read. `is_rvalue_source` is
    not this question: it answers the C++ value category, and a method
    read off a temporary is an lvalue there."""
    roots = storage_roots(expr, Use.HOLDER, None, MustLend(analyzer))
    return None if roots.unknown else roots.names


def expr_lends_storage(analyzer: ValueCategoryAnalyzer,
                       expr: TpyExpr) -> bool:
    """Whether `expr` names storage PROVEN to outlive the statement it is
    read in (`proven_lend_roots`)."""
    return proven_lend_roots(analyzer, expr) is not None
