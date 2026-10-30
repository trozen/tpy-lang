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
  one, nothing for a fresh result or an `Own` one holding no pointer.
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

from enum import Enum, Flag, auto
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
    is_str_type, is_str_view_type, is_string_type, is_varargs, get_type_def,
    type_def_of,
)
from ..typesys import (
    FunctionInfo, LiteralType, NominalType, OptionalType, OwnType,
    PendingGenericInstanceType, PendingViewType, PtrType,
    ReadonlyType, TpyType, TupleType, TypeParamRef, UnionType,
    contains_type_param, declares_no_writes, held_whole_borrow_sources,
    is_bodyless_binding, is_bufferless_scalar, is_void_like_type,
    is_dyn_protocol, is_open_type_param_return, is_protocol_type,
    recorded_return_borrow_sources, return_borrow_fact_pending,
    substitute_type_params_structural,
    bound_as_spelled, unwrap_own, unwrap_qualifiers, unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync, view_is_inherently_const, is_indirection_type,
)
from ..value_category import (
    CONTAINER_LITERAL_NODES, CallOperands, ExprTypeOf,
    ValueCategoryAnalyzer, borrowing_ctor_arg, call_operands,
    call_returns_cpp_ref, declared_result_is_value, frame_factory_callee,
    getitem_call_of,
    lent_operand_binds_open_param,
    lent_operands, property_access_returns_cpp_ref,
    holds_no_pointer, may_hold_pointer, reference_may_hold_pointer,
)
from . import own_copy
from .storage_walk import (
    qualifier_kind,
    Ask, CallPolicy, LeafKind, StorageKey, Use, call_lends_receiver_at,
    handle_position, operand_keys, storage_roots,
)
from ..storage_trace import Step, StepKind, trace
from .type_ops import param_type_can_lend, signature_may_return_borrow

if TYPE_CHECKING:
    from .context import SemanticContext
    from ..typesys import RecordInfo, TypeRegistry


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


def member_holds_root(registry: 'TypeRegistry', owner_type: 'TpyType | None',
                      member: str) -> bool:
    """Whether a write through `member` of a value of `owner_type` writes
    that value's own storage -- the one fact a write climbs a member path
    by. Read off declarations alone, so the answer never depends on which
    body sema analyzed first: a declared field holds unless its type is a
    handle (`Ptr`, a borrowing view: the write lands in the referent); a
    property holds when one of its getters hands out a writable borrow
    (`getter_holds_root`). A member this cannot find holds -- the safe
    answer is a mutable root, never a const one a write needs."""
    return _member_hop(registry, owner_type, member)[0]


def _member_hop(registry: 'TypeRegistry', owner_type: 'TpyType | None',
                member: str) -> 'tuple[bool, bool, TpyType | None]':
    """One hop of a member path off a value of `owner_type`: whether a
    write through it writes that value (`member_holds_root`), whether it is
    a field of handle type -- what a borrow through it reaches is the
    referent's, not the value's -- and the type the hop yields (None when
    unknown)."""
    record = _member_owner_record(registry, owner_type)
    decl = (_member_decl(registry, record, member)
            if record is not None else None)
    if decl is None:
        return True, False, None
    if isinstance(decl, list):
        return (any(getter_holds_root(fi) for fi in decl), False,
                decl[0].return_type if decl else None)
    handle = is_indirection_type(decl)
    return not handle, handle, decl


def getter_holds_root(fi: FunctionInfo) -> bool:
    """Whether a write through what getter `fi` returns writes its
    receiver: not for a `@readonly` / `@pure` getter, nor for a declared
    return that is fresh (`Own[...]`, a value with nothing borrowed in it),
    a handle or readonly; every other return -- a reference, a borrow-form
    tuple into the receiver -- lends the receiver writable."""
    if fi.is_pure or fi.is_readonly:
        return False
    rt = fi.return_type
    if rt is None:
        return True
    bare = unwrap_ref_type(rt)
    if isinstance(bare, OwnType) or is_indirection_type(bare):
        return False
    return escaping_borrow_writes(rt)


def key_holds_root(registry: 'TypeRegistry', root_type: 'TpyType | None',
                   members: tuple[str, ...], *, writes: bool = True) -> bool:
    """`member_holds_root` along a member path off a root of `root_type`:
    the path writes its root only when every hop does. Without `writes`,
    whether the path reaches its root at all -- a borrow or a returned
    reference through it -- which only a handle field stops."""
    cur = root_type
    for member in members:
        holds, handle, cur = _member_hop(registry, cur, member)
        if handle or (writes and not holds):
            return False
    return True


def path_holds_root(registry: 'TypeRegistry', expr: TpyExpr,
                    type_of: 'ExprTypeOf') -> bool:
    """`member_holds_root` along the place `expr` spells, each hop asked of
    the type its owner has THERE (a narrowed root included): every field
    and getter hop holds, and no element is read out of a handle a member
    holds. An element of the root handle itself (`s[0]`, a `*args` pack's
    `xs[0][0]`) is what was handed in, so the effect travels with the root's
    name (`handle_position`, as the write climb asks it)."""
    t = trace(expr)
    for i, step in enumerate(t.steps):
        if step.kind is StepKind.FIELD:
            owner = (step.node.unbound_self_parent_type
                     or type_of(step.node.obj))
            if not member_holds_root(registry, owner, step.node.field):
                return False
        elif step.kind is StepKind.GETTER:
            if not member_holds_root(registry, type_of(step.node.obj),
                                     step.node.method):
                return False
        elif (step.kind is StepKind.INDEX
              and is_indirection_type(type_of(step.node.obj))
              and handle_position(t, i + 1, type_of) != len(t.steps)):
            return False
    return True


def _member_owner_record(registry: 'TypeRegistry',
                         owner_type: 'TpyType | None') -> 'RecordInfo | None':
    """The record whose members a value of `owner_type` has, past its
    qualifiers, an `Own` and a (narrowed) `Optional`."""
    if owner_type is None:
        return None
    bare = unwrap_ref_type(unwrap_qualifiers(owner_type))
    if isinstance(bare, OwnType):
        bare = unwrap_ref_type(unwrap_qualifiers(bare.wrapped))
    if isinstance(bare, OptionalType):
        bare = unwrap_ref_type(unwrap_qualifiers(bare.inner))
    if not isinstance(bare, NominalType):
        return None
    return registry.get_record_for_type(bare)


def _member_decl(registry: 'TypeRegistry', record: 'RecordInfo', member: str
                 ) -> 'TpyType | list[FunctionInfo] | None':
    """A field's declared type, or a property's getters, by name through
    the MRO; None when `record` declares neither."""
    for info in registry.get_all_fields(record):
        if info.name == member:
            return info.type
    for owner in (record, *registry.iter_ancestor_records(record)):
        prop = owner.properties.get(member)
        if prop is not None:
            return [fi for fi in prop.accessors if fi.is_property_getter]
    return None


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


def callee_fact(fi: FunctionInfo) -> CalleeFact:
    """THE classification of a resolved callee by the borrow facts it
    carries: a callable value, a body not analyzed yet, a callee recording
    no fact (a body-less binding), or its recorded sources (empty or not).
    `callee_lend_fact` and the retention verdict (`retention_channel`) both
    read it."""
    if fi.is_callable_value:
        return CalleeFact.CALLABLE
    if return_borrow_fact_pending(fi):
        return CalleeFact.PENDING
    if fi.root.return_borrows_from is None:
        return CalleeFact.STUB
    return (CalleeFact.RECORDED if recorded_return_borrow_sources(fi)
            else CalleeFact.KNOWN_EMPTY)


def callee_lend_fact(call: TpyExpr) -> CalleeLend:
    """THE classification of what a call's callee says about the operands
    its result may point into (`callee_fact`); every call policy maps this
    one value to its own answer rather than re-deciding it."""
    access = _callee_access(call)
    ops = call_operands(call)
    if ops is None:
        return CalleeLend(CalleeFact.UNRESOLVED, None, frozenset(), False,
                          False, access)
    fi = ops.fi
    rt = fi.return_type
    declares_view = rt is not None and is_borrowing_view_type(
        unwrap_readonly(rt))
    fresh = isinstance(call, TpyCallLike) and call.result_form.is_fresh
    return CalleeLend(callee_fact(fi), ops,
                      recorded_return_borrow_sources(fi), declares_view,
                      fresh, access)


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


def _result_points(fi: FunctionInfo) -> bool:
    """Whether a call's result may point into existing storage, whether or
    not an operand lends it: a C++ reference (what a constructor builds is
    a fresh object, `_builds_object`), or a result pointing by value
    (`_points_by_value`)."""
    rt = fi.return_type
    return not _builds_object(fi) and (
        call_returns_cpp_ref(None, fi)
        or (rt is not None
            and _points_by_value(unwrap_readonly(unwrap_send_sync(rt)))))


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
    first. Nothing for a fresh result; for one declared owned whole, the
    operands it carries (`_owned_result_carries`, the rule a body-less
    binding's result channel reads too); every operand for a call no
    callee resolves. `lend` is the call's `callee_lend_fact`; `expr_type`
    and `temp_backing` are `call_lend_sources`'; a temp-backed element
    stays whatever its type, as the storage dying with the statement."""
    if lend.fresh:
        return []
    ops = lend.ops
    owned = ops is not None and _declared_own(ops.fi.root.return_type)
    result = ops.fi.return_type if ops is not None else None
    # No operand (None: any) when the owned result can carry no pointer.
    if owned and not _owned_result_carries(result, None):
        return []
    if ops is None:
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return []
        recv = [call.obj] if isinstance(call, TpyMethodCall) else []
        srcs = ([LendSource(-1, e, None) for e in recv]
                + [LendSource(i, a, None) for i, a in enumerate(call.args)]
                + [LendSource(-2, a, None)
                   for a in (call.kwargs or {}).values()])
    else:
        declared = ops.fi.root.params
        srcs = [src for src in call_lend_sources(ops, None,
                                                 expr_type=expr_type,
                                                 temp_backing=temp_backing)
                if not (0 <= src.idx < len(declared)
                        and _declared_own(declared[src.idx].type))]
    return [s for s in srcs
            if s.temp_backed or (t := type_of(s.expr)) is None
            or (_owned_result_carries(result, t) if owned
                else not holds_no_pointer(t))]


def _component_may_point(t: TpyType | None) -> bool:
    """Whether a value handed to a callee at a slot of this (substituted)
    type -- the value itself, or the elements, keys and values it hands
    over (`Own` peeled, a protocol by its type arguments) -- may hold a
    pointer by value (`may_hold_pointer`). A copy of such a value is the
    borrow it carries. Unknown (no slot, a protocol with no type argument)
    may."""
    if t is None:
        return True
    # A container literal not decided yet holds what the container it
    # spells holds.
    t = bound_as_spelled(unwrap_qualifiers(t), lists=True)
    if isinstance(t, PendingViewType):
        # A `str` / `bytes` whose form is not decided yet (a `T` slot
        # instantiated at a `str` local or literal): a `str` / `bytes`
        # parameter, whose view a binding never keeps.
        return False
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return any(_component_may_point(m) for m in t.inner_types())
    if is_varargs(t):
        # A `*args` pack's backing array is the call's own storage, never
        # kept; what a binding may keep is a copy of an element.
        return _component_may_point(t.type_args[0])
    if is_protocol_type(t) or is_dyn_protocol(t):
        args = [a for a in getattr(t, "type_args", ()) if isinstance(a, TpyType)]
        return not args or any(_component_may_point(a) for a in args)
    return may_hold_pointer(t)


def _owned_result_carries(result: TpyType | None,
                          operand: TpyType | None) -> bool:
    """Whether an owned result -- one pointing into no operand's storage --
    still carries the borrows of an operand handed to it at type `operand`
    (a slot, or the argument's own type): only through a pointer copied out
    of it, so when the result may hold a pointer (`may_hold_pointer`) and
    the operand's components may hold one (`_component_may_point`). A copy
    of a borrow is the borrow; an unknown type may."""
    return ((result is None or may_hold_pointer(result))
            and _component_may_point(operand))


def _builds_object(fi: FunctionInfo) -> bool:
    """A constructor slot: what the call hands back is the object it
    builds, not its declared return (`str(v)`, `list(xs)`)."""
    return fi.is_constructor or (
        fi.is_method and fi.name == "__init__" and not fi.is_staticmethod)


def _retention_slot(fi: FunctionInfo, index: int, call: TpyExpr | None,
                    type_of: ExprTypeOf | None,
                    arg: TpyExpr | None,
                    recv: TpyExpr | None) -> TpyType | None:
    """The substituted type at which callee `fi` takes operand `index` (-1:
    the receiver, typed at `call`), or None when unknown. A bare type
    parameter or structural protocol slot is instantiated at its argument
    `arg`'s type. A slot still naming its owning type's parameters is
    substituted at the type the call reaches the callee through: the
    receiver's (`recv`, else the member call's), or the one a constructor
    builds."""
    owner = None
    if recv is None and isinstance(call, TpyMethodCall):
        recv = call.obj
    if type_of is not None:
        owner = (type_of(recv) if recv is not None
                 else type_of(call) if call is not None and _builds_object(fi)
                 else None)
    if index == -1:
        return owner
    slot = fi.params[index].type if 0 <= index < len(fi.params) else None
    bare_slot = unwrap_qualifiers(slot) if slot is not None else None
    if (arg is not None and type_of is not None
            and (isinstance(bare_slot, TypeParamRef)
                 or (bare_slot is not None and is_protocol_type(bare_slot)
                     and not getattr(bare_slot, "type_args", None)))):
        return type_of(arg) or slot
    if slot is None or owner is None or not contains_type_param(slot):
        return slot
    bare = unwrap_qualifiers(owner)
    td = type_def_of(bare) if isinstance(bare, NominalType) else None
    rec = td.record if td is not None else None
    if rec is None or len(rec.type_params) != len(bare.type_args):
        return slot
    return substitute_type_params_structural(
        slot, dict(zip(rec.type_params, bare.type_args)))


def _stub_result_retains(fi: FunctionInfo, index: int, slot: TpyType | None,
                         call: TpyExpr | None,
                         type_of: ExprTypeOf | None) -> bool:
    """The RESULT channel of a body-less binding: whether its result may
    keep operand `index`. What the binding declares (`borrows=` /
    `element_of=`, stamped with the iterator and receiver conventions at
    registration) holds first; a declared borrow at a value-shaped
    instantiation is a fresh value (`declared_result_is_value`) unless the
    value may hold a pointer (a view result). A borrowing constructor keeps
    its first argument (`borrowing_ctor_arg`); a result that is nothing
    keeps nothing; a reference or a pointing-by-value result borrows the
    receiver of a method and the lendable operands of a free binding; an
    owned result that may hold a pointer carries the borrows of the
    operands whose components may hold one (`_owned_result_carries`)."""
    root = fi.root
    stamped = root.return_borrows_from or frozenset()
    if root.borrow_declared:
        return index in stamped and (
            not declared_result_is_value(fi)
            or fi.return_type is None or may_hold_pointer(fi.return_type))
    if index in stamped:
        return True
    if (call is not None and index == 0
            and borrowing_ctor_arg(call) is not None):
        return True
    if _builds_object(fi):
        result = type_of(call) if call is not None and type_of else None
    else:
        result = fi.return_type
        if result is None or is_void_like_type(result):
            return False
        if _result_points(fi):
            if fi.is_method and not fi.is_staticmethod:
                return index == -1
            return slot is None or param_type_can_lend(slot)
    return _owned_result_carries(result, slot)


def _declared_result_points_nowhere(fi: FunctionInfo, call: TpyExpr | None,
                                    type_of: ExprTypeOf | None) -> bool:
    """Whether a binding's result channel is a declared borrow whose value
    form holds no pointer: where an argument lends nothing the result binds
    as that value (`ResultForm`), which then keeps nothing of it. A view
    result (`Span`, `StrView`) is still the pointer as a value."""
    if not fi.root.borrow_declared:
        return False
    result = (type_of(call) if call is not None and type_of is not None
              else None) or fi.return_type
    return result is not None and not may_hold_pointer(result)


def _runs_binding(fi: FunctionInfo) -> bool:
    """Whether what a call to `fi` runs is a binding's code rather than a
    TPy body sema measures: a body-less binding, or a `...` def registered
    as the callee itself (a builtin exception's `__init__`) -- not a
    protocol's, which a conformer's body answers. Whether a TPy body backs
    the callee decides, not the render: a user operator dunder carries its
    set's shared operator spelling as a `cpp_template`, yet runs its body."""
    body = fi.root.body
    if body is not None and not is_bodyless_binding(body):
        return False
    if is_bodyless_binding(fi):
        return True
    if body is None:
        return False
    td = get_type_def(fi.owning_type_qname) if fi.owning_type_qname else None
    return td is None or td.protocol is None


def callee_retains_arg(fi: 'FunctionInfo | None', index: int,
                       call: TpyExpr | None = None,
                       type_of: ExprTypeOf | None = None,
                       arg: TpyExpr | None = None,
                       recv: TpyExpr | None = None) -> bool:
    """THE retention verdict: whether callee `fi`, substituted at `call`,
    may keep operand `index` (-1: the receiver) alive past the call -- so
    a borrow of storage the statement kills may not be handed to it.

    A callee with a body answers from what sema measured of it: its result
    borrows the parameter (`return_borrows_from`) or the body took the
    parameter's address (`addr_escapes_params`). A fact not computed yet,
    an unresolved callee and a callable value retain (fail closed).

    A body-less binding answers from its signature and binding keywords,
    never from its name, in three channels:
    - RESULT (`_stub_result_retains`), whatever else it declares --
      DECLARED_RESULT when the binding declares the borrow and the
      result's value form holds no pointer;
    - COPIES: a callee that may write anything may keep a copy of every
      value it is handed (`@readonly` is no exemption: it may stash into
      a global), and a copy of a borrow is the borrow -- so the operand is
      kept when its slot's components may hold a pointer
      (`_component_may_point`). `transient` / `@pure` promise it keeps
      nothing; the receiver is not kept this way (what it holds points
      outside it);
    - ADDRESS: never. A binding does not keep the address of a plain
      reference parameter (or of any part of it) nor the view behind a
      `str` / `bytes` parameter; one that does spells the parameter
      `Ptr[T]` / `StrView` / `Span` (docs/NATIVE_INTEROP.md).
    `type_of` types the receiver and a constructor's result at `call`, and
    the argument `arg` a bare type-parameter or protocol slot is
    instantiated at; without them those answers fail closed. `recv` is the
    receiver of a member call no call node states (an operator's dunder,
    a subscript's)."""
    return retention_channel(fi, index, call, type_of, arg,
                             recv) is not None


class Retention(Flag):
    """The channels through which a callee keeps an argument
    (`retention_channel`); a binding may keep one through several."""
    # A bodied callee's measured facts (or none computed yet), an
    # unresolved callee, a callable value or a frame's receiver.
    MEASURED = auto()
    # A body-less binding's result keeps it.
    RESULT = auto()
    # A body-less binding's DECLARED borrow (`borrows=` / `element_of=`)
    # whose value form holds no pointer keeps it: the result binds as a
    # borrow only where every argument it names lends (`ResultForm`), so
    # an argument that lends nothing is not held past the call.
    DECLARED_RESULT = auto()
    # A body-less binding may keep a copy of it, and so of every pointer it
    # holds; the receiver is never kept this way.
    COPIES = auto()


def retention_channel(fi: 'FunctionInfo | None', index: int,
                      call: TpyExpr | None = None,
                      type_of: ExprTypeOf | None = None,
                      arg: TpyExpr | None = None,
                      recv: TpyExpr | None = None) -> Retention | None:
    """`callee_retains_arg`'s verdict with every channel that keeps the
    operand, None when the callee keeps nothing of it. The callee is
    classified once (`callee_fact`); the channels are computed from it."""
    if fi is None or index < -1:
        return Retention.MEASURED
    kind = callee_fact(fi)
    # A generator or coroutine frame keeps its receiver, which no fact
    # names (BUGS.md#generator-method-receiver-borrow-unrepresented).
    if kind is CalleeFact.CALLABLE or (index == -1 and _frame_callee(fi)):
        return Retention.MEASURED
    root = fi.root
    # The one place this verdict departs from the classification: whether
    # a binding's code runs (`_runs_binding`), not whether it records a
    # fact. A binding's conventions stamped at registration classify it
    # RECORDED, which the storage walk reads as its recorded sources; here
    # they are one declaration among a binding's channels (TODO.md "The
    # result channel of the retention verdict as the storage walk's STUB
    # answer").
    if not _runs_binding(fi):
        # A body sema measures; none measured yet (PENDING) or none
        # recorded (STUB) fails closed.
        if (kind not in _RECORDS_A_FACT
                or index in recorded_return_borrow_sources(fi)
                or index in root.addr_escapes_params):
            return Retention.MEASURED
        return None
    if arg is None and isinstance(call, (TpyCall, TpyMethodCall)) and (
            0 <= index < len(call.args)):
        arg = call.args[index]
    slot = _retention_slot(fi, index, call, type_of, arg, recv)
    keeps = Retention(0)
    if _stub_result_retains(fi, index, slot, call, type_of):
        keeps |= (Retention.DECLARED_RESULT
                  if _declared_result_points_nowhere(fi, call, type_of)
                  else Retention.RESULT)
    if not (index == -1 or fi.is_pure or fi.is_transient or root.is_pure
            or root.is_transient or not _component_may_point(slot)):
        keeps |= Retention.COPIES
    return keeps or None


def _immutable_at(ctx: 'SemanticContext', expr: TpyExpr) -> bool:
    """An operand nothing can write through: one holding no pointer (a
    number, an owned `str`) or a view that is const by its type."""
    t = ctx.get_expr_type(expr)
    if t is None:
        return False
    t = unwrap_qualifiers(t)
    return (holds_no_pointer(t) or isinstance(t, PendingViewType)
            or view_is_inherently_const(t))


def _frame_callee(fi: FunctionInfo) -> bool:
    """Whether a call to `fi` builds a generator or coroutine frame."""
    return fi.is_async or frame_factory_callee(fi)


def _builds_frame(call: TpyExpr) -> bool:
    """Whether the call builds a generator or coroutine frame (a generator
    expression builds one through the call sema made for it)."""
    fi = call_callee(call)
    return fi is not None and _frame_callee(fi)


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
        # viewing it (`p.split("/")`) writes nothing of it. A getter's
        # declaration alone says whether a write through its result writes
        # its receiver (`getter_holds_root`), whichever body sema analyzed
        # first.
        frame = _builds_frame(call)
        ro_getter = (is_property_getter_read(call)
                     and not getter_holds_root(call.resolved_function_info))
        return [s._replace(grants_write=False)
                if frame or _immutable_at(self.ctx, s.expr)
                or (ro_getter and s.idx == -1) else s
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
        if not srcs and (getter or _result_points(fi)):
            return None
        # A readonly method writes nothing through its receiver, so a write
        # through its result is not one it lends of the receiver -- unless the
        # declaration gives the result its receiver's access. Of an argument
        # only a callee that writes nothing BY DECLARATION says so
        # (`declares_no_writes`: `return iter(self.items)` keeps a method
        # const); a bodied callee's `is_readonly` speaks for its receiver.
        no_writes = declares_no_writes(fi)
        receiver_writes = declared or not fi.is_readonly
        return [s._replace(declared_only=only_declared,
                           grants_write=receiver_writes,
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
    # A generic construction a later use completes is typed pending until
    # then, however the callee is spelled (`cls(xs)`, an import alias).
    if isinstance(ctx.get_expr_type(call), PendingGenericInstanceType):
        return True
    if lend.kind is CalleeFact.UNRESOLVED:
        return False
    fi = lend.ops.fi
    callable_value = lend.kind is CalleeFact.CALLABLE
    if fi.is_constructor:
        return True
    if (isinstance(call, TpyCallLike)
            and call.result_form.is_declared):
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
                and call.result_form.is_declared):
            if call.result_form is not ResultForm.BORROW:
                return None
        elif not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        elif (lend.kind not in _RECORDS_A_FACT
                or not _result_points(lend.ops.fi)):
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
