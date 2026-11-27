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
    TpyCall, TpyCallLike, TpyCoerce, TpyDictLiteral, TpyExpr, TpyFieldAccess,
    TpyFString, TpyGeneratorExpression, TpyLambda, TpyListRepeat,
    TpyMethodCall, TpyName, TpyNoneLiteral, TpySetLiteral, TpySlice,
    TpyStrLiteral,
    TpySubscript, TpyUnaryOp, TpyVarargPack, is_property_getter_read,
)
from .. import qnames
from ..coercions import OperandStorage
from ..type_def_registry import (
    is_borrowing_view_type, is_bytearray_type, is_bytes_type, is_enum_type,
    is_bytes_view_type, is_str_type, is_str_view_type, is_string_type,
    is_varargs, get_type_def, type_def_of,
)
from ..typesys import (
    type_parts_any, is_owned_shape, repr_shape,
    contains_reference_type, FunctionInfo, NominalType, OptionalType, OwnType,
    PendingViewType, PtrType,
    ReadonlyType, TpyType, TupleType, TypeParamRef, UnionType,
    body_open_type_params, contains_type_param, declares_no_writes,
    held_whole_borrow_sources,
    is_bodyless_binding, is_bufferless_scalar, is_void_like_type,
    is_dyn_protocol, is_open_type_param_return, is_protocol_type,
    recorded_return_borrow_sources, return_borrow_fact_pending,
    substitute_type_params_structural,
    bound_as_spelled, unwrap_optional_own, unwrap_own, unwrap_qualifiers,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync, view_is_inherently_const, is_indirection_type,
    NoneType,
)
from ..value_category import (
    protocol_binding,
    CONTAINER_LITERAL_NODES, CallOperands, ExprTypeOf,
    ValueCategoryAnalyzer, borrowing_ctor_arg, call_operands,
    call_returns_cpp_ref, declared_result_is_value, frame_factory_callee,
    DeclaredOf, frame_param_lends, getitem_call_of, param_type_can_lend,
    is_generic_protocol, param_written, protocol_twin_slot,
    lent_operand_binds_open_param, iterator_source_callee,
    is_iterator_protocol,
    lent_operands, method_owner_type, property_access_returns_cpp_ref,
    holds_no_pointer, is_lvalue_iterable, is_rvalue_source,
    materializing_temp_source, may_hold_pointer, peel_coerce,
    origin, OriginKind, OwningArg, carries_borrow, element_borrow_form,
    reference_may_hold_pointer, result_carries_borrow,
)
from .source_records import read_record
from .storage_walk import (
    qualifier_kind,
    Ask, CallPolicy, LeafKind, StorageKey, Use, call_lends_receiver_at,
    handle_position, operand_keys, storage_roots,
)
from ..storage_trace import Step, StepKind, trace
from .type_ops import signature_may_return_borrow

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
    return (contains_reference_type(slot)
            or reference_may_hold_pointer(r))



def declared_reader(ctx: ValueCategoryAnalyzer) -> 'DeclaredOf | None':
    """The declared-type reader of `ctx`'s binding scope, which a
    protocol argument's twin is asked at (`protocol_twin_slot`): sema's
    context has one; the lowering's analyzers, whose scopes are their own,
    have none."""
    return getattr(ctx, "declared_type", None)


def call_lend_sources(ops: CallOperands,
                      indices: 'frozenset[int] | set[int] | None' = None,
                      held: 'frozenset[int]' = frozenset(), *,
                      analyzer: 'ValueCategoryAnalyzer | None',
                      declared_of: 'DeclaredOf | None',
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
    `tuple_literal_elems`. `analyzer`'s types
    let `lent_operands` see a `Ptr[T]` operand, which grants no write; a
    caller reading `grants_write` passes it, one that does not passes None.
    It also types the operand a frame holds at a structural protocol
    parameter as that protocol's concrete twin (`protocol_twin_slot`);
    with None the protocol's own, conservative, loan stands. `declared_of`
    names a local's declared type, which a narrowed Optional or union
    operand is held at there (None: the asker has no binding scope).

    That answers "which caller storage does the result borrow". A consumer
    asking "can the result point into a temporary" passes `temp_backing`
    and also gets the owned elements of a tuple-literal operand, flagged
    `temp_backed` -- only those the result's type can point into
    (`temp_element_can_back`), and none when the call builds a frame,
    which holds the tuple by value for as long as the handle lives."""
    fi = ops.fi
    expr_type = analyzer.get_expr_type if analyzer is not None else None
    frame = fi.is_async or frame_factory_callee(fi)
    temp_backing = temp_backing and not frame
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
        if frame:
            # A frame holds a protocol argument as its concrete twin's frame
            # would: it lends only where that twin's would.
            twin = protocol_twin_slot(
                slot, arg, analyzer, declared_of, frame=True,
                writes=param_written(fi, idx),
                binding=protocol_binding(fi))
            if twin is not slot:
                if not frame_param_lends(twin):
                    continue
                slot = twin
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
    # A callable value, read by its contract as a body-less stub is: its
    # result may point into any operand it holds (`_contract_sources`), and
    # that answer is durable whichever body was analyzed first.
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


def callee_lend_fact(call: TpyExpr,
                     registry: 'TypeRegistry | None' = None) -> CalleeLend:
    """THE classification of what a call's callee says about the operands
    its result may point into (`callee_fact`); every call policy maps this
    one value to its own answer rather than re-deciding it. The sources
    are the call's answer at its instantiation (`call_result_lenders`):
    a recorded fact the instantiation empties is a known-empty one. With
    no `registry` they are the declaration's."""
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
    kind = callee_fact(fi)
    sources = call_result_lenders(fi, registry)
    if kind is CalleeFact.RECORDED and not sources:
        kind = CalleeFact.KNOWN_EMPTY
    return CalleeLend(kind, ops, sources, declares_view, fresh, access)


def _owned_at_instantiation(fi: FunctionInfo) -> bool:
    """A bodied callee's `-> T` result of a type parameter it declares
    itself, instantiated at `T = Own[X]`: the signature hands the value
    over (`val_or_ref_t` renders it by value), as a declared `-> Own[X]`
    does; a class TPy emits counts its own (`body_open_type_params`). A
    body-less binding renders its own result."""
    root = fi.root
    declared = unwrap_readonly(unwrap_ref_type(root.return_type))
    return (isinstance(declared, TypeParamRef)
            and declared.name in body_open_type_params(
                root.type_params or (),
                root.owning_type_qname if root.is_method else None)
            and not is_bodyless_binding(root)
            and isinstance(unwrap_readonly(fi.return_type), OwnType))


# The callee facts that record a `return_borrows_from`, empty or not.
_RECORDS_A_FACT = frozenset({CalleeFact.RECORDED, CalleeFact.KNOWN_EMPTY})
# No callee body to read a fact off: no callee, or a callable value.
_NO_CALLEE_BODY = frozenset({CalleeFact.UNRESOLVED, CalleeFact.CALLABLE})
# The callee facts a durability reading cannot trust: no callee, or a body
# not analyzed yet. A callable value's contract is durable.
_NO_DURABLE_FACT = frozenset({CalleeFact.UNRESOLVED, CalleeFact.PENDING})
# The callee facts that name which operands the result may point into:
# recorded sources, or a callable value's contract (every operand).
_NAMES_OPERANDS = _RECORDS_A_FACT | {CalleeFact.CALLABLE}


def _opaque_callee(lend: CalleeLend) -> bool:
    """A call through a callee expression whose result no contract
    describes (`make()(s)` of a resolved function); a callable value's
    result is its contract's however the call reaches it."""
    return (lend.access is CalleeAccess.EXPRESSION
            and lend.kind is not CalleeFact.CALLABLE)


def _proof_sources(lend: CalleeLend, srcs: list[LendSource]) -> bool:
    """Whether `srcs` name every operand the call's result may point into,
    as a proof reading needs: a body's recorded sources, or the contract
    of a callable value returning a rule-R borrow, whose result roots only
    in its arguments (`CallableType.returns_borrow`)."""
    if all(s.how is Lent.RECORDED for s in srcs):
        return True
    return (lend.kind is CalleeFact.CALLABLE
            and lend.ops.fi.callable_type.returns_borrow()
            and all(s.how is Lent.CONTRACT for s in srcs))


def _named_sources(lend: CalleeLend) -> frozenset[int] | None:
    """The operand positions a `_NAMES_OPERANDS` fact names: the recorded
    ones, None (every operand) for a contract."""
    return None if lend.kind is CalleeFact.CALLABLE else lend.sources


def _view_names_operands(lend: CalleeLend) -> bool:
    """Whether a view-returning call's callee fact says which operands its
    view reads: recorded sources (a fresh stamp's included) or a callable
    value's contract through a name or a field (never the field's
    holder). Any other says nothing: the view is the callee's storage."""
    # A view a callee expression hands back may read that temporary
    # callee's own copied state (a std::function's capture), which no
    # operand holds.
    return lend.kind is CalleeFact.RECORDED or (
        lend.kind is CalleeFact.CALLABLE
        and lend.access is not CalleeAccess.EXPRESSION)


def borrow_declared_sources(call: TpyCallLike,
                            analyzer: ValueCategoryAnalyzer,
                            declared_of: 'DeclaredOf | None'
                            ) -> list[LendSource] | None:
    """The operands a call to a binding that DECLARES its result's borrow
    sources (`result=` "borrow" / "element") lends by that declaration --
    what `stamp_result_borrow` decides the call's result form from; None for
    a call whose result is already decided fresh."""
    lend = callee_lend_fact(call, analyzer.registry)
    if lend.kind is CalleeFact.UNRESOLVED or lend.fresh:
        return None
    return call_lend_sources(lend.ops, lend.sources, analyzer=analyzer,
                             declared_of=declared_of)


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


def _owned_whole(fi: FunctionInfo) -> bool:
    """Whether `fi`'s result is owned whole by its signature: declared
    `Own[...]`, or instantiated so (`_owned_at_instantiation`)."""
    return _declared_own(fi.root.return_type) or _owned_at_instantiation(fi)


def _carries_nothing(lend: CalleeLend) -> bool:
    """Whether a call's result points into nothing under its contract: a
    fresh one, or one declared owned whole that can carry no pointer
    (`_owned_result_carries` of any operand)."""
    ops = lend.ops
    return lend.fresh or (
        ops is not None and _owned_whole(ops.fi)
        and not _owned_result_carries(ops.fi.return_type, None))


def _contract_result_points(ctx: 'SemanticContext', call: TpyExpr,
                            lend: CalleeLend) -> bool:
    """Whether a resolved call's result may point into existing storage
    under its signature alone: it is a reference or holds a pointer, and
    the contract does not make it fresh (`_carries_nothing`)."""
    rt = ctx.get_expr_type(call)
    return ((_result_points(lend.ops.fi)
             or (rt is not None and may_hold_pointer(rt)))
            and not _carries_nothing(lend))


def _contract_sources(call: TpyExpr, lend: CalleeLend, type_of: ExprTypeOf,
                      analyzer: 'ValueCategoryAnalyzer | None',
                      declared_of: 'DeclaredOf | None', *,
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
    callee resolves. `lend` is the call's `callee_lend_fact`; `analyzer`
    and `temp_backing` are `call_lend_sources`'; a temp-backed element
    stays whatever its type, as the storage dying with the statement."""
    if _carries_nothing(lend):
        return []
    ops = lend.ops
    owned = ops is not None and _owned_whole(ops.fi)
    result = ops.fi.return_type if ops is not None else None
    if ops is None:
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return []
        recv = [call.receiver] if isinstance(call, TpyMethodCall) else []
        srcs = ([LendSource(-1, e, None) for e in recv]
                + [LendSource(i, a, None) for i, a in enumerate(call.args)]
                + [LendSource(-2, a, None)
                   for a in (call.kwargs or {}).values()])
    else:
        declared = ops.fi.root.params
        srcs = [src for src in call_lend_sources(ops, None,
                                                 analyzer=analyzer,
                                                 declared_of=declared_of,
                                                 temp_backing=temp_backing)
                if not (0 <= src.idx < len(declared)
                        and _declared_own(declared[src.idx].type))]
    return [s for s in srcs
            if s.temp_backed or (t := type_of(s.expr)) is None
            or (_owned_result_carries(result, t) if owned
                else not holds_no_pointer(t))]


def contract_lend_sources(ctx: 'SemanticContext', call: TpyExpr,
                          lend: CalleeLend,
                          analyzer: 'ValueCategoryAnalyzer | None'
                          ) -> list[LendSource] | None:
    """THE reading of a call's result by its callee's signature alone, as
    a body-less stub's: nothing when the result neither is a reference nor
    holds a pointer, else every operand its contract names
    (`_contract_sources`), flagged `Lent.CONTRACT`; None when no callee
    resolves. `lend` is the call's `callee_lend_fact`, `analyzer`
    `call_lend_sources`'."""
    if lend.kind is CalleeFact.UNRESOLVED:
        return None
    if not _contract_result_points(ctx, call, lend):
        return []
    return [s._replace(how=Lent.CONTRACT)
            for s in _contract_sources(call, lend, ctx.get_expr_type,
                                       analyzer, declared_reader(ctx),
                                       temp_backing=True)]


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
                    analyzer: 'ValueCategoryAnalyzer | None',
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
    type_of = analyzer.get_expr_type if analyzer is not None else None
    if recv is None and isinstance(call, TpyMethodCall):
        recv = call.receiver
    if type_of is not None:
        owner = (type_of(recv) if recv is not None
                 else type_of(call) if call is not None and _builds_object(fi)
                 else None)
    if index == -1:
        return owner
    slot = fi.params[index].type if 0 <= index < len(fi.params) else None
    bare_slot = unwrap_qualifiers(slot) if slot is not None else None
    if arg is not None and type_of is not None:
        if isinstance(bare_slot, TypeParamRef) or (
                is_dyn_protocol(bare_slot)
                and not getattr(bare_slot, "type_args", None)):
            return type_of(arg) or slot
        # Asked by the lowering of a runtime binding's call (no frame), which
        # has no sema binding scope; a named argument's storage outlives the
        # statement, so its retention decides no temporary's. At a generic
        # protocol the object binds in place: what the binding may copy out
        # of it is an element, which the slot's type arguments name, never
        # the object's own storage.
        if not is_generic_protocol(slot):
            twin = protocol_twin_slot(slot, arg, analyzer, None, frame=False,
                                      writes=param_written(fi, index),
                                      binding=protocol_binding(fi))
            if twin is not slot:
                return twin
    if slot is None or owner is None or not contains_type_param(slot):
        return slot
    bare = unwrap_qualifiers(owner)
    td = type_def_of(bare) if isinstance(bare, NominalType) else None
    rec = td.record if td is not None else None
    if rec is None or len(rec.type_params) != len(bare.type_args):
        return slot
    return substitute_type_params_structural(
        slot, dict(zip(rec.type_params, bare.type_args)))


def lends_by_signature(fi: FunctionInfo) -> bool:
    """Whether a binding's result lends by its signature alone
    (`stub_result_lenders`): a body-less stub declaring no borrow, which no
    other rule answers (a frame, a lazy combinator's retention, a
    constructor's object; a free iterator stub keeps its arguments as a
    frame does, `TypeRegistrar._stamp_iterator_retention`)."""
    root = fi.root
    # A binding, not a protocol method's signature: the conforming type's
    # own method answers a protocol call.
    binds = (root.native_name is not None or root.is_native
             or root.cpp_template is not None)
    return (binds and is_bodyless_binding(root) and not root.borrow_declared
            and not (root.is_generator or root.is_async)
            and not root.is_constructor
            and not (iterator_source_callee(root) and not root.is_method))


def stub_result_lenders(fi: FunctionInfo,
                        registry: 'TypeRegistry') -> frozenset[int]:
    """The stamp of a body-less stub's DECLARED signature: the operands
    (the receiver at -1) its result may point into, whatever its TPy twin
    could return -- the receiver and every operand that can hold the
    result's referent (`_holds_referent`), except an operand its `args=`
    places ("compared" / "stored"). An open `T` may hold anything; a call
    reads the stamp at its instantiation (`call_result_lenders`)."""
    if not _result_may_point(fi):
        return frozenset()
    root = fi.root
    decl = root.variant_decl
    placed = ({name for name, _ in decl.declared_args or ()}
              if decl is not None else set())
    out: set[int] = set()
    if (root.is_method and not root.is_staticmethod
            and not root.is_consuming):
        out.add(-1)
    referents = _result_referents(fi)
    out.update(i for i, p in enumerate(fi.params)
               if p.name not in placed
               and _holds_referent(p.type, referents, registry))
    return frozenset(out)


def call_result_lenders(fi: FunctionInfo,
                        registry: 'TypeRegistry | None') -> frozenset[int]:
    """THE lending answer at a call: the operands (the receiver at -1) the
    result of a call to `fi` may point into, read at `fi`'s INSTANTIATED
    signature. Of what the callee says its result may borrow (its
    `return_borrows_from`: a body's recorded sources, a binding's declared
    `result=`, a stub's stamp), each keeps only what can hold the result's
    referent there (`_holds_referent`): `pick[T](a: T, b: T) -> T` at
    `int32` lends nothing, as its concrete twin. A generator or coroutine
    result is its frame, which holds what the frame captures by reference
    (`frame_param_lends`). A declared `result=` is believed as written (what the C++ hands
    back is the binding's to state: a reinterpreting cast). With no
    registry, or at the declaration itself, it is the stamp."""
    root = fi.root
    stamped = root.return_borrows_from or frozenset()
    if (not stamped or fi is root or registry is None
            or root.borrow_declared):
        return stamped
    if _stamped_as_frame(root):
        return frozenset(
            i for i in stamped
            if not 0 <= i < len(fi.params)
            or frame_param_lends(fi.params[i].type))
    if (contains_type_param(root.return_type)
            and not _result_may_point(fi)):
        return frozenset()
    referents = _result_referents(fi)
    return frozenset(
        i for i in stamped
        if not 0 <= i < len(fi.params)
        or _holds_referent(fi.params[i].type, referents, registry))


def _result_shape(t: TpyType) -> TpyType:
    """A result's shape under its access and marker wrappers
    (`repr_shape`); an owned one (`Own[...]`) keeps its wrapper, since it
    points nowhere."""
    return t if is_owned_shape(t) else repr_shape(t)


def _result_may_point(fi: FunctionInfo) -> bool:
    """Whether a result at `fi`'s signature may point into an operand: it
    renders a reference, a pointer, a view or an iterator handle -- an
    Optional, a union or a tuple holding one included -- or is an open `T`
    that may be one. What a constructor builds, or nothing, points
    nowhere."""
    if (fi.return_type is None or is_void_like_type(fi.return_type)
            or _builds_object(fi)):
        return False
    rt = _result_shape(fi.return_type)
    if isinstance(rt, (OptionalType, UnionType, TupleType)):
        return any(_part_points(m) for m in rt.inner_types())
    return (_result_points(fi) or is_iterator_protocol(rt)
            or is_open_type_param_return(rt) or _part_points(rt))


def _part_points(t: TpyType) -> bool:
    """A part of a result that points: a reference, a pointer or view, an
    iterator handle, an open `T`, or an Optional / union / tuple with such a
    part; an owned part (`Own[...]`) and an owned `str` / `bytes` value
    hold their own storage. A reference type (`bytearray`, `String`) is a
    reference here."""
    if is_owned_shape(t):
        return False
    t = repr_shape(t)
    if isinstance(t, (OptionalType, UnionType, TupleType)):
        return any(_part_points(m) for m in t.inner_types())
    if isinstance(t, NoneType) or is_str_type(t) or is_bytes_type(t):
        return False
    return (not t.is_value_type() or may_hold_pointer(t)
            or is_iterator_protocol(t) or isinstance(t, TypeParamRef))


def _result_referents(fi: FunctionInfo) -> 'list[TpyType] | None':
    """What a pointing result points at (`_referents`), or None for a
    result that views storage rather than being an object: a view, a
    string or byte view, an iterator handle, or an Optional / union of
    one."""
    rt = _result_shape(fi.return_type)
    members = ([repr_shape(m) for m in rt.inner_types()
                if not isinstance(m, NoneType)]
               if isinstance(rt, (OptionalType, UnionType)) else [rt])
    if any(is_borrowing_view_type(m) or is_str_view_type(m)
           or is_bytes_view_type(m) or is_iterator_protocol(m)
           for m in members):
        return None
    return _referents(rt)


def _holds_referent(ptype: TpyType | None, referents: 'list[TpyType] | None',
                    registry: 'TypeRegistry') -> bool:
    """Whether an argument of `ptype` can be or hold what a result points
    at. It must be able to lend storage (`param_type_can_lend`, a `Ptr` and
    a `String` included; an owned argument, moved in, lends only what a
    pointer or view it holds points at); a
    view result may then view it, and so may a pointer to a value or to an
    open `T` (an owned buffer lends into itself) unless the argument is a
    scalar nothing can point into (`is_bufferless_scalar`).
    A reference-type referent lives only in an argument reaching
    (`type_parts_any`) a reference type -- a subclass, a field's object --
    an open `T`, or a part that may hold a pointer (`may_hold_pointer`: an
    opaque native object, a view field)."""
    if ptype is None or not param_type_can_lend(ptype, pointers=True):
        return False
    # An owned argument is moved in, but a pointer or view it holds still
    # points where it did.
    if is_owned_shape(ptype) and not may_hold_pointer(repr_shape(ptype)):
        return False
    if referents is None:
        return True
    bare = repr_shape(ptype)
    # An open `T` referent may be instantiated at a value type, and the
    # instantiation only filters this stamp, never adds to it.
    if any(r.is_value_type() or isinstance(r, TypeParamRef)
           for r in referents):
        return not is_bufferless_scalar(bare)
    return type_parts_any(
        bare, lambda x: isinstance(x, TypeParamRef) or not x.is_value_type()
        or (isinstance(x, NominalType) and not isinstance(x, PtrType)
            and may_hold_pointer(x)), registry)


def _referents(result: TpyType) -> 'list[TpyType]':
    """What a reference result points at (each under its wrappers,
    `repr_shape`): itself; a pointer's pointee, each member of an Optional
    / union pointee; for an Optional / union / tuple what each of its
    pointing members (`_part_points`) points at."""
    result = repr_shape(result)
    if isinstance(result, PtrType):
        pointee = repr_shape(result.pointee)
        if isinstance(pointee, (OptionalType, UnionType)):
            return [repr_shape(m) for m in pointee.inner_types()
                    if not isinstance(repr_shape(m), NoneType)]
        return [pointee]
    if isinstance(result, (OptionalType, UnionType, TupleType)):
        return [r for m in result.inner_types() if _part_points(m)
                for r in _referents(m)]
    return [result]


def _stub_result_retains(fi: FunctionInfo, index: int, slot: TpyType | None,
                         call: TpyExpr | None,
                         type_of: ExprTypeOf | None,
                         registry: 'TypeRegistry | None') -> bool:
    """The RESULT channel of a body-less binding: whether its result may
    keep operand `index`. What the binding declares (`result=` "borrow" /
    "element", stamped with the iterator and receiver conventions at
    registration) holds first; a declared borrow at a value-shaped
    instantiation is a fresh value (`declared_result_is_value`) unless the
    value may hold a pointer (a view result). A borrowing constructor keeps
    its first argument (`borrowing_ctor_arg`); a result that is nothing
    keeps nothing; a reference or a pointing-by-value result borrows the
    receiver of a method and the lendable operands of a free binding; an
    owned result that may hold a pointer carries the borrows of the
    operands whose components may hold one (`_owned_result_carries`)."""
    root = fi.root
    lenders = call_result_lenders(fi, registry)
    if root.borrow_declared:
        return index in lenders and (
            not declared_result_is_value(fi)
            or fi.return_type is None or may_hold_pointer(fi.return_type))
    if index in lenders:
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
    return _owned_result_carries(result, slot)


def slot_can_lend(slot: TpyType | None) -> bool:
    """Whether an argument at parameter type `slot` can be storage a
    result points into: a parameter that takes a value (a number) is not
    (`param_type_can_lend`); an unknown slot may be."""
    return slot is None or param_type_can_lend(slot)


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
                       analyzer: 'ValueCategoryAnalyzer | None' = None,
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
    `analyzer` types the receiver and a constructor's result at `call`, and
    the argument `arg` a bare type-parameter or protocol slot is
    instantiated at; without them those answers fail closed. `recv` is the
    receiver of a member call no call node states (an operator's dunder,
    a subscript's)."""
    return retention_channel(fi, index, call, analyzer, arg,
                             recv) is not None


class Retention(Flag):
    """The channels through which a callee keeps an argument
    (`retention_channel`); a binding may keep one through several."""
    # A bodied callee's measured facts (or none computed yet), an
    # unresolved callee, a callable value or a frame's receiver.
    MEASURED = auto()
    # A body-less binding's result keeps it.
    RESULT = auto()
    # A body-less binding's DECLARED borrow (`result=` "borrow"/"element")
    # whose value form holds no pointer keeps it: the result binds as a
    # borrow only where every argument it names lends (`ResultForm`), so
    # an argument that lends nothing is not held past the call.
    DECLARED_RESULT = auto()
    # A body-less binding may keep a copy of it, and so of every pointer it
    # holds; the receiver is never kept this way.
    COPIES = auto()
    # Beside MEASURED: what the body was measured to keep it through is its
    # result only (a recorded return-borrow source whose address escapes
    # nowhere else).
    MEASURED_RESULT = auto()
    # Beside MEASURED: a constructor stores it in the object it builds and
    # nowhere else the body is measured to (its `self` fields only).
    CONSTRUCTED = auto()
    # An `Iterable` / `Iterator` parameter whose iterator the result may
    # still advance after the call (`enumerate(xs)`, `itertools.cycle([..])`)
    # and so point into the argument: what the body or binding is measured
    # to keep names the parameter alone.
    ITERATED = auto()


def retention_channel(fi: 'FunctionInfo | None', index: int,
                      call: TpyExpr | None = None,
                      analyzer: 'ValueCategoryAnalyzer | None' = None,
                      arg: TpyExpr | None = None,
                      recv: TpyExpr | None = None) -> Retention | None:
    """`callee_retains_arg`'s verdict with every channel that keeps the
    operand, None when the callee keeps nothing of it. The callee is
    classified once (`callee_fact`); the channels are computed from it."""
    if fi is None or index < -1:
        return Retention.MEASURED
    keeps = _measured_retention(fi, index, call, analyzer, arg, recv)
    if keeps is not None and keeps & (Retention.RESULT
                                      | Retention.DECLARED_RESULT):
        # A binding's result channel already keeps it.
        return keeps
    if arg is None and isinstance(call, (TpyCall, TpyMethodCall)) and (
            0 <= index < len(call.args)):
        arg = call.args[index]
    if _iterated_lazily(fi, index, call, analyzer, arg):
        return (keeps or Retention(0)) | Retention.ITERATED
    return keeps


def _iterated_lazily(fi: 'FunctionInfo', index: int, call: TpyExpr | None,
                     analyzer: 'ValueCategoryAnalyzer | None',
                     arg: TpyExpr | None) -> bool:
    """Parameter `index` is an `Iterable` / `Iterator` (or an Optional of
    one) whose result may iterate it after the call returns (`ITERATED`):
    an object known by its protocol (an iterator, a generator or
    coroutine) or one a binding's constructor builds (`_runs_binding`: a
    TPy `__init__`'s field stores are measured) -- unless the result takes
    the argument over (`_handed_over`). A result of any other type was built
    by the call, which iterated the argument then (`acc += xs`). Without
    an analyzer no result type is known: nothing is added."""
    if analyzer is None or not 0 <= index < len(fi.params):
        return False
    t = analyzer.get_expr_type(call) if isinstance(call, TpyExpr) else None
    if not ((fi.initializes_type and _runs_binding(fi))
            or _frame_callee(fi) or (
            t is not None and is_protocol_type(
                unwrap_readonly(unwrap_send_sync(t))))):
        return False
    ptype = fi.params[index].type
    if ptype is None:
        return False
    bare = unwrap_readonly(unwrap_send_sync(ptype))
    if isinstance(bare, OptionalType):
        bare = unwrap_readonly(bare.inner)
    if not (isinstance(bare, NominalType) and bare.is_protocol
            and bare.qualified_name() in (qnames.ITERABLE, qnames.ITERATOR)):
        return False
    return (not _handed_over(fi, index, arg, analyzer)
            and result_may_borrow(call, fi, analyzer))


def _measured_retention(fi: 'FunctionInfo', index: int,
                        call: TpyExpr | None,
                        analyzer: 'ValueCategoryAnalyzer | None',
                        arg: TpyExpr | None,
                        recv: TpyExpr | None) -> Retention | None:
    """The channels `retention_channel` reads off the callee's measured
    facts and binding keywords."""
    kind = callee_fact(fi)
    # A generator or coroutine frame keeps its receiver, which no fact
    # names (BUGS.md#generator-method-receiver-borrow-unrepresented).
    if kind is CalleeFact.CALLABLE or (index == -1 and _frame_callee(fi)):
        return Retention.MEASURED
    root = fi.root
    if root.keeps_no_argument:
        return None
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
        escapes = (index in root.addr_escapes_params
                   or index in root.readonly_addr_escapes_params)
        if kind not in _RECORDS_A_FACT:
            return Retention.MEASURED
        if escapes:
            return (Retention.MEASURED | Retention.CONSTRUCTED
                    if fi.initializes_type
                    and index not in root.foreign_addr_escapes_params
                    else Retention.MEASURED)
        if index in recorded_return_borrow_sources(fi) or (
                index in call_result_lenders(
                    fi, analyzer.registry if analyzer is not None else None)):
            return Retention.MEASURED | Retention.MEASURED_RESULT
        return None
    if arg is None and isinstance(call, (TpyCall, TpyMethodCall)) and (
            0 <= index < len(call.args)):
        arg = call.args[index]
    slot = _retention_slot(fi, index, call, analyzer, arg, recv)
    type_of = analyzer.get_expr_type if analyzer is not None else None
    keeps = Retention(0)
    registry = analyzer.registry if analyzer is not None else None
    if _stub_result_retains(fi, index, slot, call, type_of,
                            registry) and not (
            _adopts_fresh(fi, index, arg, analyzer)):
        keeps |= (Retention.DECLARED_RESULT
                  if _declared_result_points_nowhere(fi, call, type_of)
                  else Retention.RESULT)
    if not (index == -1 or fi.is_pure or fi.is_transient or root.is_pure
            or root.is_transient or not _component_may_point(slot)):
        keeps |= Retention.COPIES
    return keeps or None


def result_may_borrow(e: object, fi: FunctionInfo | None,
                       analyzer: ValueCategoryAnalyzer) -> bool:
    """Whether `e`'s value may point into an operand's storage: a value
    that may hold a pointer, a reference callee `fi` returns, or -- with no
    callee, a field or element read -- a reference-type object, which is
    the operand's own storage."""
    t = analyzer.get_expr_type(e) if isinstance(e, TpyExpr) else None
    if t is not None and is_protocol_type(
            unwrap_readonly(unwrap_send_sync(t))):
        # An object known by its protocol (an iterator over its source, a
        # generator or coroutine) may hold what it was given.
        return True
    if fi is not None:
        return result_carries_borrow(fi, t)
    return carries_borrow(t, borrow_form=t is None or element_borrow_form(
        t, copies=False))


def _handed_over(fi: FunctionInfo, index: int, operand: object,
                 analyzer: ValueCategoryAnalyzer) -> bool:
    """A fresh argument the result takes over whole -- a container its
    owning element slot is handed (`handed_over_record`), one a binding
    adopts (`adopts=`): the result owns it, nothing points into it."""
    if handed_over_record(operand, analyzer):
        return True
    # A record whose `__iter__` lends a separate iterator into it is pinned
    # where its iterator is: adopting it moves nothing that iterator reads.
    return (isinstance(operand, TpyExpr)
            and index in fi.root.adopted_params
            and is_rvalue_source(analyzer, peel_coerce(operand))
            and separate_iterator_record_temp(operand, analyzer) is None)


def handed_over_record(operand: object,
                       analyzer: ValueCategoryAnalyzer) -> bool:
    """`operand` is handed over to an owning element slot (its source
    record's `handover`)."""
    # Sema asks with its own context, the lowering with an analyzer over it.
    ctx = getattr(analyzer, "ctx", analyzer)
    rec = read_record(ctx, operand) if isinstance(
        operand, TpyExpr) else None
    return rec is not None and rec.handover is OwningArg.HANDED_OVER


def _self_iterator_record(ri, analyzer) -> bool:
    """Whether a compiled record is its own iterator: every `__iter__` it
    declares or inherits returns a REFERENCE to exactly this record. An
    ancestor's type does not count -- `return self` inherited from a base and
    `return self.inner` delegating to a base-typed member have the same
    signature -- and neither does a fresh instance of its own class
    (`-> Own[Cur]`). The runtime asks the same question of the C++ type
    (`is_self_iterator_v`, dunder.hpp)."""
    overloads = analyzer.registry.get_method_overloads_with_parents(
        ri, "__iter__")
    if not overloads:
        return False
    for fi in overloads:
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            fi.return_type)))
        if isinstance(rt, OwnType):
            return False
        if analyzer.registry.get_record_for_type(rt) is not ri:
            return False
    return True


def separate_iterator_record_temp(a: TpyExpr, analyzer) -> 'TpyType | None':
    """A TEMPORARY of a compiled record whose `__iter__` returns a separate
    iterator object: the record type, else None. Its `__iter__` is user code
    that runs at the combinator call, and the iterator may point into the
    record, so a combinator that OWNS one is pinned -- the pair cannot move.
    A self-iterator record, a runtime or @native type and an lvalue are never
    this kind."""
    at = analyzer.get_expr_type(a)
    if at is None:
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    if not isinstance(at, NominalType) or at.is_protocol:
        return None
    ri = analyzer.registry.get_record_for_type(at)
    if ri is None or ri.is_native:
        return None
    if not analyzer.registry.get_method_overloads_with_parents(ri, "__iter__"):
        return None
    if _self_iterator_record(ri, analyzer):
        return None
    if is_lvalue_iterable(a, analyzer.registry.get_record):
        return None
    return at



def _adopts_fresh(fi: FunctionInfo, index: int, arg: TpyExpr | None,
                  analyzer: 'ValueCategoryAnalyzer | None') -> bool:
    """Whether the binding's result takes over argument `arg` at parameter
    `index` (`result=` "adopts"): a fresh one is moved into the result, so
    the result channel keeps nothing of its storage -- the loans the argument
    itself carries are its own operands' and stay with it. Without an
    analyzer to tell a fresh argument from a named one it fails closed."""
    return (index in fi.root.adopted_params and arg is not None
            and analyzer is not None
            and is_rvalue_source(analyzer, peel_coerce(arg)))


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


def _stamped_as_frame(fi: FunctionInfo) -> bool:
    """Whether `fi`'s stamp is what a frame captures by reference
    (`generator_borrow_param_indices`): a generator or coroutine, or a free
    body-less iterator source (`zip`, `enumerate`), whose C++ object keeps
    its arguments as a frame does."""
    return _frame_callee(fi) or (is_bodyless_binding(fi) and not fi.is_method
                                 and iterator_source_callee(fi))


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
        if ask is Ask.LOAN:
            # A frame handed to an owning parameter passes its loans on: the
            # result (a Task the frame runs in) may keep it.
            handed = _handed_frames(call)
            if handed:
                srcs = (srcs or []) + handed
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
        lend = callee_lend_fact(call, self.ctx.registry)
        kind = lend.kind
        if lend.fresh or (lend.ops is not None
                          and _owned_at_instantiation(lend.ops.fi)
                          and _carries_nothing(lend)):
            return []
        ctor_arg = borrowing_ctor_arg(call)
        if ctor_arg is not None:
            return [_ctor_lend(ctor_arg, ctx.get_expr_type(call))]
        typed = ctx if ask is not Ask.LIFETIME else None
        if kind in _NO_CALLEE_BODY:
            # A callable value: its contract (`contract_lend_sources`); an
            # open `T` is left out until instantiation-time facts exist
            # (`acc = func(acc, x)` in `functools.reduce` would keep `acc`).
            rt = ctx.get_expr_type(call)
            if rt is None or contains_type_param(rt):
                return []
            return contract_lend_sources(ctx, call, lend, typed) or []
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
                                                typed, declared_reader(ctx))
                     if not (declared and s.idx == -1)]
                    if signature_may_return_borrow(fi) else [])
            if declared:
                srcs.insert(0, LendSource(-1, ops.obj, None,
                                          declared_only=True,
                                          how=Lent.CONVENTION))
            return srcs
        stub = kind is CalleeFact.STUB
        if stub:
            # No fact and no binding behind the signature (a protocol
            # method's): the conforming type's method answers the call, so
            # only a reference it returns is taken to be into its receiver.
            indices = (frozenset({-1}) if getter
                       or call_returns_cpp_ref(ctx, fi) else frozenset())
            if ops.obj is None:
                indices = indices - {-1}
        else:
            indices = lend.sources
        only_declared = stub or (declared and -1 not in indices)
        if declared:
            indices = indices | {-1}
        srcs = call_lend_sources(ops, indices, held_whole_borrow_sources(fi),
                                 analyzer=typed, temp_backing=True,
                                 declared_of=declared_reader(ctx))
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


def coercion_reads_operand(c: TpyCoerce) -> bool:
    """Whether coercion `c` hands back its operand's storage rather than a
    value of its own (its rule's `OperandStorage.READS`: a view
    conversion, a deref of a `Ptr`, a pointer conversion)."""
    return c.coercion.operand_storage is OperandStorage.READS


def peel_reading_coercions(e: TpyExpr) -> TpyExpr:
    """`e` past the coercions that hand back their operand's storage
    (`coercion_reads_operand`): what is the same storage as `e`."""
    while isinstance(e, TpyCoerce) and coercion_reads_operand(e):
        e = e.expr
    return e


def fresh_receiver_root(obj: TpyExpr,
                        ctx: 'SemanticContext') -> TpyExpr | None:
    """The fresh object a member reached through receiver `obj` lives in:
    `obj` itself, or the materializing temporary under its chain of field
    reads, calls and coercions that build nothing (a Deref hop adds no
    node). A field, and an element no callee reads (a tuple's), is part of
    its object. A call -- a subscript
    and a slice are `__getitem__` calls, a getter is one too -- is part of
    its receiver where the call policy (`MayLend`) says its result may
    point into the receiver, else its own object when it materializes one,
    the same for every type. None where the chain reaches storage that
    outlives the statement (a reference the policy places in no operand),
    or a pointer, which names its object."""
    found: list[TpyExpr] = []
    _walk_fresh(obj, ctx, found, None)
    return found[0] if found else None


def fresh_operand_roots(obj: TpyExpr, ctx: 'SemanticContext',
                        builds_nothing: 'Callable[[TpyCoerce], bool]'
                        ) -> 'tuple[TpyExpr, ...]':
    """Every fresh object `obj` reads, by `fresh_receiver_root`'s walk,
    which here also passes a call whose result materializes nothing into
    each operand it may point into -- its receiver and every argument the
    call policy names, in evaluation order (`head(s + "x")` of a
    `-> StrView` head lending its parameter, `pick2(a + "x", b + "y")`) --
    and a conversion that builds nothing at its position
    (`builds_nothing`: a `str` argument's view of a string) into its
    operand."""
    found: list[TpyExpr] = []
    _walk_fresh(obj, ctx, found, builds_nothing)
    return tuple(found)


def _lent_arguments(srcs: 'list[LendSource] | None') -> 'list[TpyExpr]':
    """The arguments among a call's lend sources, in argument order."""
    lent = {id(s.expr): (s.idx, s.expr) for s in srcs or ()
            if s.idx >= 0 and not s.temp_backed}
    return [e for _, e in sorted(lent.values(), key=lambda p: p[0])]


def _walk_fresh(n: TpyExpr, ctx: 'SemanticContext', found: 'list[TpyExpr]',
                builds_nothing: 'Callable[[TpyCoerce], bool] | None') -> None:
    """The fresh objects `n` reads, appended to `found`; `builds_nothing`
    None walks `fresh_receiver_root`'s one chain."""
    through_operands = builds_nothing is not None
    policy = MayLend(ctx)
    while True:
        peeled = peel_reading_coercions(n)
        while (through_operands and isinstance(peeled, TpyCoerce)
               and builds_nothing(peeled)):
            peeled = peel_reading_coercions(peeled.expr)
        if any(t is not None and t.is_pointer()
               for t in (ctx.get_expr_type(n), ctx.get_expr_type(peeled))):
            # A deref of a `Ptr` reads storage the pointer names.
            return
        n = peeled
        ops = call_operands(n) if isinstance(
            n, (TpySubscript, TpyMethodCall)) else None
        if isinstance(n, TpyFieldAccess) and n.class_constant_owner is not None:
            # A class constant is a static: it reads no storage of its
            # receiver.
            return
        if isinstance(n, TpyFieldAccess) or (isinstance(n, TpySubscript)
                                             and ops is None):
            # No callee: a tuple element or a TypedDict field.
            n = n.obj
            continue
        if ops is not None and ops.obj is not None:
            srcs = policy.at_call(n, (), Ask.LIFETIME)
            if srcs and any(s.idx == -1 for s in srcs):
                if through_operands:
                    _walk_fresh(ops.obj, ctx, found, builds_nothing)
                    for a in _lent_arguments(srcs):
                        _walk_fresh(a, ctx, found, builds_nothing)
                    return
                n = ops.obj
                continue
        if materializing_temp_source(n, ctx):
            found.append(n)
            return
        if through_operands and isinstance(n, (TpyCall, TpyMethodCall)):
            srcs = policy.at_call(n, (), Ask.LIFETIME)
            if srcs and all(s.idx >= 0 for s in srcs):
                for a in _lent_arguments(srcs):
                    _walk_fresh(a, ctx, found, builds_nothing)
        return


def _handed_frames(call: TpyExpr) -> list[LendSource]:
    """The generator or coroutine frames `call` takes ownership of: an
    argument that builds a frame (`frame_call`) at an `Own` parameter. The
    frame's own loans are its operands', so a result that may keep the
    frame keeps them."""
    ops = call_operands(call)
    if ops is None:
        return []
    params = ops.fi.params
    out: list[LendSource] = []
    for idx, arg in ops.indexed():
        if not 0 <= idx < len(params):
            continue
        slot = params[idx].type
        if (unwrap_optional_own(unwrap_readonly(slot)) is not None
                and frame_call(peel_coerce(arg)) is not None):
            out.append(LendSource(idx, arg, slot, held_whole=True,
                                  grants_write=False))
    return out


def frame_call(call: TpyExpr) -> 'TpyCall | TpyMethodCall | None':
    """The call that builds a generator or coroutine frame, when `call` is
    one -- a generator expression builds its frame through the call sema
    made for it -- else None."""
    if isinstance(call, TpyGeneratorExpression):
        call = call.frame_creation
    return (call if isinstance(call, (TpyCall, TpyMethodCall))
            and _builds_frame(call) else None)


def frame_operands(call: TpyExpr, analyzer: 'ValueCategoryAnalyzer | None',
                   declared_of: 'DeclaredOf | None'
                   ) -> list[TpyExpr] | None:
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
        out.append(frame.receiver)
    lend = callee_lend_fact(
        frame, analyzer.registry if analyzer is not None else None)
    ops = CallOperands(fi, None, list(frame.args), frame.kwargs or {})
    out += [s.expr for s in call_lend_sources(
        ops, lend.sources if lend.kind in _RECORDS_A_FACT else None,
        analyzer=analyzer, declared_of=declared_of)]
    return out


def frame_operand_keys(call: TpyExpr,
                       analyzer: 'ValueCategoryAnalyzer | None',
                       declared_of: 'DeclaredOf | None'
                       ) -> list[StorageKey]:
    """The loan key of each operand the frame `call` builds keeps
    (`frame_operands`; a `*args` pack lends one per element), none when
    `call` builds no frame."""
    return [key for e in frame_operands(call, analyzer, declared_of) or ()
            for key in operand_keys(e)]


class ViewLend(CallPolicy):
    """VIEW: the operands a call's result may point into when that result
    is a view of a `str` / `bytes` buffer -- the BUFFER question: every
    operand but a bufferless scalar may own a buffer the view reads, where
    MAY asks whether an operand holds a POINTER (`holds_no_pointer`). A
    non-view result is a fresh value: no operands. A view result answers its recorded borrow sources,
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
        if not isinstance(call, (TpyCall, TpyMethodCall, TpySubscript)):
            return None
        rt = self.ctx.get_expr_type(call)
        if rt is None or not is_borrowing_view_type(unwrap_readonly(rt)):
            return []
        lend = callee_lend_fact(call, self.ctx.registry)
        if lend.ops is None:
            return None
        if lend.kind is CalleeFact.RECORDED and not lend.fresh:
            return [s._replace(how=Lent.RECORDED)
                    for s in call_lend_sources(
                        lend.ops, lend.sources,
                        analyzer=self.ctx,
                        declared_of=declared_reader(self.ctx),
                        temp_backing=True)
                    ] or None
        # A subscript is the `__getitem__` call its operands name.
        operands = [LendSource(i, a, None, how=Lent.CONTRACT)
                    for i, a in enumerate(lend.ops.args) if a is not None]
        operands += [LendSource(-2, a, None, how=Lent.CONTRACT)
                     for a in (lend.ops.kwargs or {}).values()]
        # A callable value's receiver operand is its callee held by
        # reference, never a field's holder (`call_operands`); a method
        # call's is the object it runs on (`TpyMethodCall.receiver`).
        recv = (lend.ops.obj if lend.kind is CalleeFact.CALLABLE
                else call.receiver if isinstance(call, TpyMethodCall)
                else call.obj if isinstance(call, TpySubscript)
                else None)
        if recv is not None and self.ctx.get_expr_type(recv) is not None:
            operands.append(LendSource(-1, recv, None, how=Lent.CONTRACT))
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
        operand (`at_call` answered `srcs`, None or empty): a fresh result
        is a TEMPORARY; a view call that may read no operand is UNNAMED, any
        other call shape UNPLACEABLE."""
        if srcs is not None:
            return LeafKind.TEMPORARY
        return (LeafKind.UNNAMED
                if isinstance(call, (TpyCall, TpyMethodCall))
                else LeafKind.UNPLACEABLE)


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
    borrow of it dies with: a value made from lent operands, or a new
    object the call proves fresh (`origin`). Without `proof`, also what a
    borrow of the result may not outlive though the call proves nothing
    else about it: an owned `str` / `String` / `bytes` / `bytearray`
    buffer and a borrowing view constructed from nothing.

    A call through a callable value whose return is an open type parameter
    is not fresh: at `U = P` it lends what its arguments lend, at
    `U = Own[P]` its value is owned and the slot takes it over."""
    if any(leaf.kind is OriginKind.COPY
           or (leaf.kind is OriginKind.NEW and leaf.proven)
           for leaf in origin(ctx, call)):
        return True
    if proof:
        return False
    if isinstance(call, TpyCall) and call.call_type is not None:
        return is_borrowing_view_type(call.call_type) and not call.args
    lend = callee_lend_fact(call, ctx.registry)
    if lend.kind is CalleeFact.UNRESOLVED or (
            isinstance(call, TpyCallLike) and call.result_form.is_declared):
        return False
    fi = lend.ops.fi
    return (is_str_type(fi.return_type) or is_string_type(fi.return_type)
            or is_bytes_type(fi.return_type)
            or is_bytearray_type(fi.return_type)
            or is_bytearray_type(unwrap_own(unwrap_readonly(
                ctx.get_expr_type(call)))))


def _is_null_ptr(call: TpyExpr) -> bool:
    return (isinstance(call, TpyCall) and isinstance(call.call_type, PtrType)
            and not call.args)


def _through_expression(call: TpyExpr) -> bool:
    """A call through a callee EXPRESSION (`make()(s)`): no name to read a
    borrow fact off."""
    return (isinstance(call, TpyCall) and not isinstance(call.func, TpyName)
            and borrowing_ctor_arg(call) is None and not _is_null_ptr(call))


def _builtin_own_result(ctx: 'SemanticContext', call: TpyExpr) -> bool:
    """An `Own[T]` result of a callee that is no user function (a free
    call) or of a method on no user record, protocol or type parameter:
    the durability readers do not call it a temporary
    (BUGS.md#user-own-factory-exempt-as-builtin)."""
    # Returned at an open `-> T` it copies at a value T and fails the C++
    # build at a record T (BUGS.md#generic-return-val-or-ref-rvalue).
    if isinstance(call, TpyCallLike) and call.result_form.is_fresh:
        return False
    if not isinstance(call, (TpyCall, TpyMethodCall)):
        return False
    fi = call.resolved_function_info
    # A callable value's name is no function the registry knows, which the
    # free-call test below would read as a builtin's; its `Own[R]` is a
    # def's.
    if (fi is None or not isinstance(fi.return_type, OwnType)
            or fi.is_callable_value):
        return False
    if isinstance(call, TpyMethodCall):
        # The receiver the method resolved on: a `Ptr`, `Box` or `Rc`
        # receiver reaches its pointee's method through `__deref__`.
        recv = method_owner_type(call, ctx.get_expr_type(call.receiver))
        # Through a protocol or a type parameter the call may reach a user
        # filler, whose `Own[T]` result is fresh like a concrete method's.
        if isinstance(recv, TypeParamRef) or is_protocol_type(recv):
            return False
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
      TEMPORARY; a callable value's call, whatever reaches the callee,
      lends its contract's sources as a body-less stub's (`MayLend`);
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

    `gen_yield` is the reading at a generator's borrow-slot yield, whose
    slot lends what it is handed for a whole step: an `Own[T]` result is a
    TEMPORARY whoever the callee (`copy()`, `xs.pop()`). `proof` is the
    reading that must PROVE a temporary (`borrow_temp_root`): a fresh
    result only by
    `call_result_is_fresh`'s proof, a call only through its recorded
    sources, no path through a hidden call, a walrus or an await, and the
    and/or arms followed."""
    follows_selects = True

    def __init__(self, ctx: 'SemanticContext', *, gen_yield: bool = False,
                 proof: bool = False) -> None:
        self.ctx = ctx
        self.gen_yield = gen_yield
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

    def end_places(self, node: TpyExpr) -> list[str] | None:
        # A closure capturing by reference (one handed to an `Fn`
        # parameter) holds the names it captures; a call through it may
        # hand back a borrow of any of them.
        if (not self.proof and isinstance(node, TpyLambda)
                and not node.captures_by_value):
            return list(node.captured_names)
        return None

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        if ((self.proof or self.gen_yield
             or not _builtin_own_result(self.ctx, call))
                and call_result_is_fresh(self.ctx, call, proof=self.proof)):
            return []
        if _is_null_ptr(call):
            return []
        if borrowing_ctor_arg(call) is not None:
            return None if self.proof else self.may.at_call(call, path, ask)
        lend = callee_lend_fact(call, self.ctx.registry)
        if _opaque_callee(lend) or lend.kind in _NO_DURABLE_FACT:
            return None
        srcs = self.may.at_call(call, path, ask)
        if not srcs or any(s.assumed for s in srcs):
            return None
        if self.proof and not _proof_sources(lend, srcs):
            return None
        return srcs

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> LeafKind:
        if srcs is not None:
            return (LeafKind.NULL if _is_null_ptr(call)
                    else LeafKind.TEMPORARY)
        if not self.proof and _opaque_callee(
                callee_lend_fact(call, self.ctx.registry)):
            return LeafKind.TEMPORARY
        return LeafKind.UNNAMED

    def at_await(self, step: Step) -> 'list[LendSource] | LeafKind':
        if self.proof:
            return LeafKind.UNPLACEABLE
        op = step.node.value
        if not isinstance(op, (TpyCall, TpyMethodCall)):
            return LeafKind.TEMPORARY
        lend = callee_lend_fact(op, self.ctx.registry)
        obj = op.receiver if isinstance(op, TpyMethodCall) else None
        if lend.kind is CalleeFact.RECORDED:
            return [s._replace(how=Lent.AWAITED) for s in call_lend_sources(
                lend.ops, lend.sources, analyzer=self.ctx,
                declared_of=declared_reader(self.ctx), temp_backing=True)]
        # Fail closed on a borrow-await with no fact: every operand.
        return [LendSource(-1 if e is obj else i, e, None,
                           how=Lent.AWAITED_ALL)
                for i, e in enumerate(([obj] if obj is not None else [])
                                      + list(op.args))]


class ContractReturnLend(ReturnLend):
    """RETURN read off each call's signature alone, so the answer
    does not depend on which body was analyzed first -- a resolved
    callee's result may point into every operand its contract names
    (`_contract_sources`, the body-less stub rule), never only into the
    ones its body is proven to return. A fresh result is a TEMPORARY, a
    result that may point but names no operand UNNAMED: storage the
    contract does not name (a closure's captures, a module variable). A
    call through a callable value whose result may point outside rule R
    (`unrooted_callable_result`) is not climbed: the leaf is the call."""

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> list[LendSource] | None:
        ctx = self.ctx
        if ((not _builtin_own_result(ctx, call)
                and call_result_is_fresh(ctx, call)) or _is_null_ptr(call)):
            return []
        if borrowing_ctor_arg(call) is not None:
            return self.may.at_call(call, path, ask)
        if unrooted_callable_result(ctx, call):
            return None
        srcs = contract_lend_sources(
            ctx, call, callee_lend_fact(call, ctx.registry), ctx)
        return None if srcs is None else srcs or None


def unrooted_callable_result(ctx: 'SemanticContext', call: TpyExpr) -> bool:
    """Whether `call` is a call through a `Callable` or `Fn` value whose
    result may point (`_contract_result_points`) but is no rule-R borrow (a
    bare reference type or `readonly`): a `Ptr[P]` or a view, which its
    signature cannot promise points only into its arguments."""
    lend = callee_lend_fact(call, ctx.registry)
    return (lend.kind is CalleeFact.CALLABLE
            and not lend.ops.fi.callable_type.returns_borrow()
            and _contract_result_points(ctx, call, lend))


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
        lend = callee_lend_fact(call, self.ctx.registry)
        if lend.kind is CalleeFact.PENDING or (
                view and not _view_names_operands(lend)):
            # Trusted by the callee's DECLARED result: a type parameter
            # instantiated at a view (`dict.pop` -> V) hands back the
            # argument it was given, not storage the callee owns.
            if not (view and lend.declares_view):
                return []
            if isinstance(call, TpyMethodCall):
                return [LendSource(-1, call.receiver, None,
                                   how=Lent.CONTRACT)]
            if isinstance(call, TpySubscript):
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
            return LeafKind.TEMPORARY
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
        return call_lend_sources(ops, call_result_lenders(
                                     ops.fi, self.ctx.registry),
                                 analyzer=self.ctx,
                                 declared_of=declared_reader(self.ctx),
                                 temp_backing=True)

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
    known -- recorded, or a callable value's contract -- and whose result
    is a C++ reference, a borrowing view or a borrow-form tuple. It climbs a plain field read, a container subscript
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
            return not (isinstance(node, TpyCoerce)
                        and node.coercion.builds_fresh_value)
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
        lend = callee_lend_fact(call, self.ctx.registry)
        if (isinstance(call, TpyCallLike)
                and call.result_form.is_declared):
            if call.result_form is not ResultForm.BORROW:
                return None
        elif not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        elif (lend.kind not in _NAMES_OPERANDS
                or not _result_points(lend.ops.fi)):
            return None
        if lend.kind is CalleeFact.UNRESOLVED:
            return None
        return call_lend_sources(lend.ops, _named_sources(lend),
                                 analyzer=self.ctx,
                                 declared_of=declared_reader(self.ctx))


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
            lend = callee_lend_fact(call, self.ctx.registry)
            if lend.kind is CalleeFact.UNRESOLVED:
                return None
            return [s for s in call_lend_sources(
                        lend.ops, None, held_whole_borrow_sources(lend.ops.fi),
                        analyzer=self.ctx,
                        declared_of=declared_reader(self.ctx))
                    if not (s.held_whole and s.idx >= 0)]
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return None
        return _contract_sources(call, callee_lend_fact(call, self.ctx.registry),
                                 self.type_of,
                                 None, declared_reader(self.ctx))


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
