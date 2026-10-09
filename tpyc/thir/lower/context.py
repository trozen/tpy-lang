"""Per-function lowering state: _Prescan, _NarrowScope, and _LowerCtx."""

from __future__ import annotations
import copy
from collections.abc import Callable, Iterator, Mapping, Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from enum import Enum, auto
from typing import NamedTuple
from ...parse.nodes import (TpyAssign, TpyExpr,
                            TpyFunction, TpyGlobal, TpyName,
                            TpyNamedExpr, TpyVarDecl)
from ...prescan import scan_reassigned_vars
from ...typesys import pointer_repr_optional
from ...type_def_registry import has_view_param_form
from ...typesys import (
    AnyType,
    CallableType,
    ConcreteFrameType,
    OptionalType,
    OwnType,
    ReadonlyType,
    RecursiveAliasInstanceType,
    TpyType,
    TypeParamRef,
    TupleType,
    UnionType,
    VoidType,
    param_takes_ownership,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp.forms import is_plain_nonvalue
from ...value_category import const_place
from ...typesys import (
    holds_borrowing_view,
    property_getter_returns_storage_ref,
    Representation,
    return_representation,
)
from ..source import UNBOUND, BindingRepr, NameFacts
from ..nodes import THIRFormConvert, THIRNarrowedRead, THIRSelf, THIRUnionExtraction, THIRUnionLayout
from .captures import CaptureSites
from .predicates import (
    _callable_value,
    _resolved_bytes_value,
    _resolved_str_value,
    _span_value,
    _borrow_tuple_return_type,
    _optional_borrow_tuple,
    _res_container_return,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _is_type_param_slot,
    _native_iter_value_slot,
    _optional_ptr_borrow,
    _record_class_binding,
    _optional_ptr_borrow_wide,
    _storage_optional_return_wide,
    _own_viewfam_param,
    _param_is_const,
    _protocol_auto_slot,
    _dyn_borrow_return,
    _own_dyn_return,
    _own_genrec_return,
    _own_wrapper_return,
    _wrapper_borrow_return,
    _own_storage_union_return,
    _own_type_param_slot,
    _viewfam_return_slots,
    _record_borrow_return,
    _record_storage_return,
    _value_record_slot,
    _value_opt_record,
    _span_return,
    _value_opt_scalar,
    _value_opt_tuple,
    _value_opt_view,
    _generic_value_tuple_return,
    _own_storage_tuple_return,
    _wrapper_ref_tuple_return,
    _value_tuple_return,
    _union_elem_value_tuple,
    _storage_copy_value,
    _value_opt_owned_view,
    _value_tuple,
    record_like,
)


class _ExprResultUse(Enum):
    VALUE = auto()
    DISCARD = auto()
    CONDITION = auto()
    TRUTHY = auto()
    STORAGE = auto()
    BORROW_BIND = auto()
    ITERABLE = auto()
    RECEIVER = auto()
    # ERASED/BORROWED await operand: the resumable skeleton immediately
    # moves/points the result into the sub-future slot (emplace(std::move..)
    # / &(..)), so a record-family result renders bare -- no value slot.
    SUSPEND = auto()
    # An `Own[...]` by-value ARG slot consuming a same-typed owning call
    # rvalue whole -- the prvalue moves through the `&&` slot bare
    # (`describe(pick(True))` for a union payload, `poll_ready(
    # sock._accept_nonblocking())` for a tuple one). Never threaded at
    # decl/return sinks (their slots gate separately).
    OWN_SLOT = auto()
    # A user-record `__setitem__` TARGET (`l[0] = z`): the checked write
    # emits the receiver and the index and never reads the element, so the
    # result shape is vacuous -- an F1-record element admits here where a
    # READ of it is the borrow-form seam and keeps rejecting.
    SETITEM_TARGET = auto()


class _RecordCtorUse(Enum):
    DIRECT = auto()
    NESTED_ARG = auto()
    RECORD_TEMP = auto()


class ValueOptKind(Enum):
    """The value-repr `std::optional<T>` LOCAL binding families -- one
    kind-tagged registration where three parallel sets used to live. The
    kinds differ only in the narrowed-deref FORM verdict (a binding record's
    `value_opt`)."""
    SCALAR = auto()
    VIEW = auto()
    RECORD = auto()


class SinkPos(Enum):
    """WHERE a lowered expression lands -- the POSITION half of the use
    channel. One member per SINK, not per lowering arm: the arms that fill
    one sink share its member and name the verdict they want through
    `forms=`, so a consumer reading `pos` learns what the sink IS rather
    than which arm happened to build the use."""
    UNSPECIFIED = auto()
    # A local binding's init or reseat, in every form the classifier gives
    # it -- value, storage, borrow, pointer slot, borrow tuple, coro frame.
    LOCAL_DECL = auto()
    # One argument at its param slot, the `__tmp_N` arg-temp inits and the
    # `copy()` sources included.
    CALL_ARG = auto()
    # The V parameter of a checked element write (`d[k] = v`).
    SETITEM_VALUE = auto()
    # The receiver of a member read, a method call or a subscript.
    RECEIVER = auto()
    # An element slot of an `Own[tuple]` container.
    TUPLE_ELEM = auto()
    # A function's return slot.
    RETURN = auto()
    # Either arm of a ternary.
    IF_EXPR_ARM = auto()
    # An element slot of a container or tuple literal.
    CONTAINER_ELEM = auto()
    # A record field's write slot.
    FIELD_WRITE = auto()
    # A field's ctor member-init slot, which direct-initializes.
    MIL_INIT = auto()
    # A module-init global's slot write.
    GLOBAL_SLOT_WRITE = auto()
    # The `auto __tup_N = <expr>;` capture a tuple unpack binds through.
    UNPACK_SOURCE = auto()
    # A resumable alias bind's source.
    ALIAS_BIND = auto()
    # An operand of a binop, a comparison or a value select.
    OPERAND = auto()
    # A monomorphized lambda's body at its trailing return.
    LAMBDA_RETURN = auto()
    # `(m = pick(nodes, i))` -- a walrus target's source.
    WALRUS_TARGET = auto()
    # A resumable frame's slot write.
    FRAME_SLOT_WRITE = auto()
    # An argument of a `print` statement, raw or under a printer wrap.
    PRINT_ARG = auto()
    # `f"{h.pair}"` -- one interpolation, under its to_str wrap.
    FSTRING_INTERP = auto()
    # `match p.choose(d):` -- the union-switch subject.
    MATCH_SUBJECT = auto()
    # `if xs:` -- a truthiness operand, in every mode.
    TRUTHINESS_OPERAND = auto()
    # `(*e).__raise__()` -- the receiver of a raise.
    RAISE_OPERAND = auto()
    # `&(*q)` -- the inner of a coercion that pre-derefs its source.
    COERCE_INNER = auto()
    # `with open(path, mode) as f` -- the manager slot, sync or emplaced
    # into a resumable frame's `__with_ctx_N`.
    WITH_MANAGER = auto()
    # `for x in <it>:` / `[x for x in <it>]` -- the ITERABLE a loop captures,
    # one member for both routes (sync for-each and resumable frame) and for
    # the comprehension family: what the capture must be able to hold is the
    # same question at all of them.
    ITER_SOURCE = auto()


class CallArgKind(Enum):
    """WHICH argument loop is filling a CALL_ARG sink. The callee's shape,
    not the sink's -- a second axis beside `SinkPos`, because the same
    param slot routes differently for a generic callee than a concrete one.
    The remaining kinds (free / method / method-stub / ctor / marker /
    protocol) are still spelled as booleans on the arg lowerer."""
    UNSPECIFIED = auto()
    # A substituted param slot of a GENERIC callee, where the concrete
    # position's routing does not hold.
    GENERIC = auto()


class SinkForm(Enum):
    """WHAT render the sink admits -- the FORM half of the use channel.
    A form recurs across positions (the ptr-Optional pass-through lands at
    twelve of them), which is why the channel is a pair: a flat sink-kind
    enum would need one member per position x form."""
    # A tuple-valued result the sink consumes WHOLE -- the
    # `auto __tup_N = <expr>;` unpack capture, an owning slot's emplace, a
    # matching tuple param. Includes the shapes no ordinary value sink
    # takes: an `Own[F1-record]`-element tuple call result
    # (`_owned_tuple_call_ret`) and a value-tuple class constant.
    TUPLE_SOURCE = auto()
    # A generator / async-def FACTORY call (`g = gen(n)`,
    # `asyncio.run(main_coro())`'s inner call), whose concrete frame is
    # consumed whole by a frame slot or the heap adapter -- never by a
    # typed value slot.
    FRAME_FACTORY = auto()
    # A @native record-returning free call into the `__ctx_N` manager slot
    # (`with open(path, mode) as f`), whatever native symbol the overload
    # resolves to.
    CTX_MANAGER = auto()
    # A stored lvalue an owning sink's copy-assign takes whole: a
    # borrow-returning record source (`h.p = identity(pt);`, and its
    # container sibling `self.mirror = h.peek();`, whose copy sema warns),
    # a field read and a container element of a record, Optional, union or
    # tuple. A decl binds REF_ALIAS off the same result, so decl sinks keep
    # rejecting.
    RECORD_COPY = auto()
    # A `T&`-returning call rendered BARE (`return get_first(items);`):
    # nothing binds off it, so the REF_ALIAS frontier does not arise.
    BORROW_RET_PASSTHROUGH = auto()
    # The same `T&` composing transiently under a member read
    # (`ret_param_ref(shared).n` -- the temp lives to the end of the full
    # expression; nothing binds).
    FIELD_RECV_BORROW = auto()
    # A generic call whose monomorphized val_or_ptr_t tuple IS the
    # closure's borrow-form trailing return, so the direct `return body;`
    # needs no element conversion.
    LAMBDA_BTUPLE_RET = auto()
    # The pure-literal binop FOLD, admitted where no slot target is threaded
    # into the render. Slot-threaded positions (decl init / arg / return)
    # render the FULL operator expression and keep rejecting it.
    LITERAL_FOLD = auto()
    # A BORROW-returning ptr-repr Optional result landing BARE: it already
    # IS the `T*` the slot or binding holds (`g = find(xs, k);`,
    # `Point* r1 = get_or_none(true, p);`). Every other consumer of such a
    # result materializes a slot or lifts through `optional_to_ptr`.
    PTR_OPT_PASSTHROUGH = auto()
    # The same result lifted by the sink through `ptr_to_optional`
    # (`h.value = find_point(pts, 1);`) instead of landing bare -- one
    # verdict cannot stand for both renders.
    PTR_OPT_LIFT = auto()
    # A ptr-variant union result the sink consumes whole through the
    # `to_value_variant` lift (`z.pet = identity(new_pet);`).
    UNION_VALUE_LIFT = auto()
    # A pointer-repr tuple result the sink binds WHOLE, with no form
    # conversion -- the `auto` decl off `pair_of(b)`, and the mixed
    # own+borrow call render (`std::tuple<Box, Box*>`) that already IS the
    # sink's shape. Every other consumer of such a result converts form.
    BTUPLE_SLOT = auto()
    # A `T&`-returning record call the sink takes the address of
    # (`p = &(get_item<Point>((*points), 0));`). A bare RECEIVER admission
    # would also open field reads off such calls (`shared(a).x`) -- the
    # REF_ALIAS place/loan frontier, design-stopped.
    ADDR_CALL = auto()
    # An ALWAYS_TRUE truthiness operand: the wrap renders it inside
    # `static_cast<void>(...)`, so the result is discarded exactly as at
    # statement position -- the same widened return set applies.
    TRUTHY_DISCARD = auto()
    # A pointer-local name read that fully derefs (`(*p)`), where the
    # default value use derefs only the always-indirect bindings. Resolved
    # in one place, `_name_read_deref`.
    INDIRECT_READ = auto()
    # A non-wrapper ptr-variant union result consumed whole by the
    # by-value dispatch local (`auto __match_subject_N = <call>;`). Every
    # other consumer of a union result converts or narrows.
    UNION_SUBJECT = auto()
    # An accessor result that LENDS storage its RECEIVER owns, where the
    # receiver is an rvalue dying at the end of the full expression -- the
    # render is a reference into it. Every TRANSIENT sink admits it: the
    # read finishes before the receiver does. No sink that BINDS or HOLDS
    # does, and neither answer a binding could give is right -- owning a
    # copy loses the later mutations CPython makes through what was lent
    # (it can outlive the receiver: a global, a longer-lived object), and
    # aliasing it dangles. The row is the whole rule; there is no
    # per-consumer subtraction.
    DYING_SOURCE_LEND = auto()
    # A VALUE-tuple FIELD read consumed whole by a `tuple_to_str` wrap,
    # where storage and borrow form coincide and the bare member read IS
    # the render. Narrower than the BORROW_BIND result use, which also
    # unlocks the record / container / pointer-repr-tuple field legs.
    FIELD_VALUE_TUPLE = auto()
    # A record / container select (ternary or and/or) rendered as a C++
    # prvalue `?:` whatever its operands: a fresh operand is built into the
    # storage, an existing-object operand is COPIED (sema warns). Only a sink
    # whose own storage copies what it is handed anyway takes it: a ctor
    # member-init, an `Own[T]` argument, a `copy()` source, an owning async
    # return. A consumer that names the object (a binding, a receiver, a
    # reference parameter) keeps the lvalue render.
    SELECT_PRVALUE = auto()
    # The same prvalue `?:`, taken only when EVERY operand is fresh, at a
    # sink that direct-initializes owned storage but would alias an existing
    # object under CPython: a plain local decl, a pointer-local's rebind
    # slot, a frame slot write, an owning return, a nested select arm of a
    # prvalue select. The select lowering decides freshness
    # (`_select_prvalue_ok`); a select with an existing-object operand keeps
    # the sink's other forms.
    SELECT_FRESH_PRVALUE = auto()
    # A select whose fresh operand is emplaced into a slot hoisted before the
    # statement (`THIRSlotEmplace`), at a local binding that aliases it
    # single-assignment in the statement's own block -- a `T&` alias -- so
    # the slot lives exactly as long as the alias. The other admitted
    # consumers are the transient ones -- a slot outlives any temporary --
    # which need no row of their own.
    SELECT_SLOT = auto()


_NO_FORMS: frozenset[SinkForm] = frozenset()
# One immutable singleton per verdict: a sink's row and a site's `forms=`
# override name the same set, so neither allocates per construction.
_ONLY_TUPLE_SOURCE: frozenset[SinkForm] = frozenset({SinkForm.TUPLE_SOURCE})
_ONLY_BTUPLE_SLOT: frozenset[SinkForm] = frozenset({SinkForm.BTUPLE_SLOT})
_ONLY_INDIRECT_READ: frozenset[SinkForm] = frozenset({SinkForm.INDIRECT_READ})
_ONLY_FRAME_FACTORY: frozenset[SinkForm] = frozenset({SinkForm.FRAME_FACTORY})
_ONLY_CTX_MANAGER: frozenset[SinkForm] = frozenset({SinkForm.CTX_MANAGER})
# The FOLD end of the 3-valued literal axis. Which sinks carry it is
# enumerated, never derived from `slot_target is None`: a target-less sink
# that did not vet the fold renders the full operator, so the two differ.
_ONLY_LITERAL_FOLD: frozenset[SinkForm] = frozenset({SinkForm.LITERAL_FOLD})
_ONLY_PTR_OPT_PASSTHROUGH: frozenset[SinkForm] = frozenset(
    {SinkForm.PTR_OPT_PASSTHROUGH})
_ONLY_PTR_OPT_LIFT: frozenset[SinkForm] = frozenset({SinkForm.PTR_OPT_LIFT})
_ONLY_UNION_VALUE_LIFT: frozenset[SinkForm] = frozenset(
    {SinkForm.UNION_VALUE_LIFT})
_ONLY_RECORD_COPY: frozenset[SinkForm] = frozenset({SinkForm.RECORD_COPY})
_ONLY_BORROW_RET_PASSTHROUGH: frozenset[SinkForm] = frozenset(
    {SinkForm.BORROW_RET_PASSTHROUGH})
_ONLY_FIELD_RECV_BORROW: frozenset[SinkForm] = frozenset(
    {SinkForm.FIELD_RECV_BORROW})
_ONLY_ADDR_CALL: frozenset[SinkForm] = frozenset({SinkForm.ADDR_CALL})
_ONLY_SELECT_PRVALUE: frozenset[SinkForm] = frozenset(
    {SinkForm.SELECT_PRVALUE})
_ONLY_SELECT_FRESH_PRVALUE: frozenset[SinkForm] = frozenset(
    {SinkForm.SELECT_FRESH_PRVALUE})
_ONLY_SELECT_SLOT: frozenset[SinkForm] = frozenset({SinkForm.SELECT_SLOT})
_ONLY_UNION_SUBJECT: frozenset[SinkForm] = frozenset({SinkForm.UNION_SUBJECT})
_ONLY_TRUTHY_DISCARD: frozenset[SinkForm] = frozenset(
    {SinkForm.TRUTHY_DISCARD})
_ONLY_LAMBDA_BTUPLE_RET: frozenset[SinkForm] = frozenset(
    {SinkForm.LAMBDA_BTUPLE_RET})
_ONLY_FIELD_VALUE_TUPLE: frozenset[SinkForm] = frozenset(
    {SinkForm.FIELD_VALUE_TUPLE})

# What each sink takes by DEFAULT -- the verdict most of its arms want. An
# arm that wants another passes `forms=`; where no verdict is more common
# the row is the plain arm's, the one the sink is named for. Total over
# SinkPos on purpose: a member added without a row is a KeyError at the
# first admission read, not a silent reject.
# A site that names its sink and passes no `forms=` inherits the row below.
# Before the pair an omitted flag REJECTED (the construct was named); now
# an omitted verdict renders whatever the row says, and several verdicts
# render BARE (a `T*` where the slot wanted `ptr_to_optional`). So a new
# site is checked against its slot, not against the row -- the per-sink
# derivation TODO.md schedules removes the hazard by deciding the verdict
# from the slot at the position. Of the two derived sinks, CALL_ARG's
# `INDIRECT_READ` is the one permissive default still inherited (at 9 sites),
# and it stays: a pointer-local NAME read derefing at an argument slot is
# what an argument POSITION is, not a property of any slot.
# The TRANSIENT verdict -- the consumer finishes inside the full expression,
# so an accessor result that lends a dying receiver's storage is still alive
# where it is read. Spelled once and OR-ed into each transient row below (and
# into the `forms=` of the transient SITES whose sink's row is not theirs), so
# "may this position hold a borrow of dying storage" is one name rather than a
# property re-derived per consumer.
_LEND_OK: frozenset[SinkForm] = frozenset({SinkForm.DYING_SOURCE_LEND})
# The second half of the same TRANSIENT verdict: nothing binds off the read,
# so a `T&`-returning call also renders BARE here -- no address is taken of
# it, nothing copies it, and the reference outlives the expression it is read
# in. One name for the pair, because both answer "does this position hold
# anything past the full expression?" and a sink that answers no answers no to
# both. Spelled at the transient SITES whose sink's row is not theirs (a
# truthiness test, a membership haystack) exactly as `_LEND_OK` is.
_TRANSIENT_OK: frozenset[SinkForm] = _LEND_OK | frozenset(
    {SinkForm.BORROW_RET_PASSTHROUGH})

_POS_FORMS: dict[SinkPos, frozenset[SinkForm]] = {
    # NOT a sink: the row every site inherits when it names none. Binding
    # sites are among them, so it FAILS CLOSED for the lend -- a transient
    # read that wants it says so with `pos=`, and a site that forgets rejects
    # loudly instead of copying borrowed storage in silence.
    SinkPos.UNSPECIFIED: _NO_FORMS,
    # Every LOCAL_DECL site derives its verdict through `_decl_slot_forms`,
    # so nothing inherits this row and an omitted `forms=` REJECTS again --
    # the default hazard the comment above names, closed for this sink. The
    # key stays: the table is total over SinkPos on purpose.
    SinkPos.LOCAL_DECL: _NO_FORMS,
    SinkPos.CALL_ARG: _ONLY_INDIRECT_READ,
    SinkPos.SETITEM_VALUE: _ONLY_TUPLE_SOURCE,
    SinkPos.RECEIVER: _ONLY_INDIRECT_READ | _LEND_OK,
    SinkPos.TUPLE_ELEM: _ONLY_TUPLE_SOURCE,
    SinkPos.RETURN: _ONLY_INDIRECT_READ,
    SinkPos.IF_EXPR_ARM: _ONLY_INDIRECT_READ,
    SinkPos.CONTAINER_ELEM: _ONLY_INDIRECT_READ,
    SinkPos.FIELD_WRITE: _ONLY_RECORD_COPY,
    SinkPos.MIL_INIT: _ONLY_TUPLE_SOURCE,
    SinkPos.GLOBAL_SLOT_WRITE: _ONLY_PTR_OPT_PASSTHROUGH,
    SinkPos.UNPACK_SOURCE: _ONLY_TUPLE_SOURCE,
    SinkPos.ALIAS_BIND: _ONLY_INDIRECT_READ,
    SinkPos.OPERAND: _ONLY_LITERAL_FOLD | _TRANSIENT_OK,
    SinkPos.LAMBDA_RETURN: _ONLY_LAMBDA_BTUPLE_RET,
    SinkPos.WALRUS_TARGET: _ONLY_TUPLE_SOURCE,
    SinkPos.FRAME_SLOT_WRITE: _ONLY_TUPLE_SOURCE,
    SinkPos.PRINT_ARG: _ONLY_PTR_OPT_PASSTHROUGH | _LEND_OK,
    SinkPos.FSTRING_INTERP: _ONLY_FIELD_VALUE_TUPLE | _LEND_OK,
    SinkPos.MATCH_SUBJECT: _ONLY_UNION_SUBJECT,
    SinkPos.TRUTHINESS_OPERAND: _ONLY_TRUTHY_DISCARD | _TRANSIENT_OK,
    SinkPos.RAISE_OPERAND: _ONLY_INDIRECT_READ | _LEND_OK,
    SinkPos.COERCE_INNER: _ONLY_INDIRECT_READ | _LEND_OK,
    SinkPos.WITH_MANAGER: _ONLY_CTX_MANAGER,
    # The capture outlives the setup statement, so it holds nothing that
    # dies with it -- `_LEND_OK` is deliberately absent.
    SinkPos.ITER_SOURCE: _NO_FORMS,
}

def _slot_lift_forms(ptr_opt_slot: bool,
                     union_slot: bool) -> 'frozenset[SinkForm] | None':
    """Which lift a field-write slot admits, read off the SLOT's type: a
    pointer-repr Optional field lifts a borrowed `T*` through
    `ptr_to_optional`, a ptr-variant union field lifts through
    `to_value_variant`, and a slot that is neither takes no lift. One sink
    position, three verdicts -- the type picks, not the position."""
    if ptr_opt_slot:
        return _ONLY_PTR_OPT_LIFT
    if union_slot:
        return _ONLY_UNION_VALUE_LIFT
    return _NO_FORMS


def _decl_slot_forms(slot: 'TpyType | None', analyzer, *,
                     whole_tuple_call: bool, from_call: bool,
                     ptr_local: bool) -> frozenset[SinkForm]:
    """Which forms a local DECL slot admits, read off the slot's type plus
    three facts the decl arm already holds -- whether the init is a call the
    sink consumes WHOLE, whether it is a call at all, and whether the name
    binds as a pointer:

      frame slot + from_call                    -> FRAME_FACTORY
      pointer slot (ptr-repr Optional, Ptr[T])  -> PTR_OPT_PASSTHROUGH
      tuple slot + whole_tuple_call             -> TUPLE_SOURCE
      borrow-form tuple slot + from_call        -> BTUPLE_SLOT
      plain non-value + ptr_local + from_call   -> BORROW_RET_PASSTHROUGH
      anything else                             -> no forms

    TUPLE_SOURCE is the one row the slot's SHAPE cannot decide: a tuple of
    OWNED tuples has no pointer-repr element, so the arm's own
    whole-consumption predicate enters as a boolean, exactly as
    `_slot_lift_forms` takes its two."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
         if slot is not None else None)
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    # FRAME_FACTORY names a FACTORY CALL's result, so the row asks for the
    # call as well as the frame slot.
    if isinstance(u, ConcreteFrameType) and from_call:
        return _ONLY_FRAME_FACTORY
    # A tuple slot is answered BEFORE the pointer row: an `Optional[tuple]`
    # of reference elements uses pointer repr (the inner is not a value
    # type), and the tuple rows -- not the bare `T*` pass -- are its
    # verdicts.
    tup = u if isinstance(u, TupleType) else None
    if tup is None and isinstance(u, OptionalType):
        opt_inner = unwrap_readonly(u.inner)
        tup = opt_inner if isinstance(opt_inner, TupleType) else None
    if tup is not None:
        if whole_tuple_call:
            return _ONLY_TUPLE_SOURCE
        if from_call and (tup.has_pointer_repr_element()
                          or tup.has_ref_elements() or tup.is_mixed_own()):
            return _ONLY_BTUPLE_SLOT
        return _NO_FORMS
    if (pointer_repr_optional(u) is not None
            or _eligible_ptr_value(slot, analyzer)):
        return _ONLY_PTR_OPT_PASSTHROUGH
    if from_call and ptr_local and u is not None and is_plain_nonvalue(u):
        return _ONLY_BORROW_RET_PASSTHROUGH
    return _NO_FORMS


def _call_arg_forms(whole_tuple: bool, coro_factory: bool,
                    ptr_opt_pass: bool) -> frozenset[SinkForm]:
    """Which forms an ARGUMENT slot admits. Unlike the decl slot's, these
    three are (source, slot) PAIR facts the admitting arm has already
    tested -- a whole-tuple source at a matching tuple param, an async-def
    factory call at an erased or structural protocol param, a ptr-Optional
    argument whose face resolves to the bare pass -- so they enter as
    booleans and the helper owns only the CHOICE of set, the way the
    receiver's does.

    All three false is the sink's own row: a pointer-local NAME read fully
    derefs at an argument slot, which is what an argument position IS, not
    a property of any slot. So the row and this helper agree by
    construction. The three are mutually exclusive at the arms that test
    them -- each returns before the next is reached."""
    if whole_tuple:
        return _ONLY_TUPLE_SOURCE
    if coro_factory:
        return _ONLY_FRAME_FACTORY
    if ptr_opt_pass:
        return _ONLY_PTR_OPT_PASSTHROUGH
    return _ONLY_INDIRECT_READ


_PTR_OPT_AND_INDIRECT = frozenset({SinkForm.PTR_OPT_PASSTHROUGH,
                                   SinkForm.INDIRECT_READ})


def _recv_forms(opt_passthrough: bool, deref: bool) -> frozenset[SinkForm]:
    """Which forms a RECEIVER admits -- the object a read or a call is taken
    OF, at a method call, a field read or a subscript. A receiver has no
    slot, so unlike the decl and argument helpers both verdicts are facts
    about the receiver EXPRESSION and the callee: whether a checked
    receiver's `T*` result lands bare in deref_check, and whether a
    pointer-local NAME derefs (a template or native callee spells `(*x)`
    where a real member call spells `->`; a narrowed ptr-Optional name and a
    pointer-slot local read their deref the same way). The two are
    independent -- all four combinations occur -- so the position admits
    both.

    Every verdict carries `_LEND_OK`: a receiver is read inside the full
    expression, so the position's own row admits an accessor result that
    lends a dying receiver's storage whichever of the two facts holds."""
    if opt_passthrough:
        base = _PTR_OPT_AND_INDIRECT if deref else _ONLY_PTR_OPT_PASSTHROUGH
    else:
        base = _ONLY_INDIRECT_READ if deref else _NO_FORMS
    return base | _LEND_OK


class SlotLifetime(Enum):
    """What a slot obliges its source's storage to outlive -- the LIFETIME
    part of the slot contract (`slot_lifetime` decides it)."""
    # The slot owns what it is handed (builds, copies or moves it), or is
    # done with it inside the statement.
    NONE = auto()
    # The slot keeps pointing into its source's buffer after the statement
    # (a `StrView` / `BytesView` / `Span` field), so that buffer must outlive
    # it. A source that only lives as long as the OBJECT holding the slot is
    # a further obligation, the MIR's to track; it gets a member with its
    # first reader.
    OUTLIVES_STATEMENT = auto()


class SlotConstruct(Enum):
    """HOW the destination receives the value -- the construction mode.
    The same source renders differently per mode: a paren direct-init
    `f(v)` resolves the slot type's constructor overloads (an explicit
    `std::string(string_view)` fires, a bare brace can pick the size
    constructor), while an assignment `this->f = v` goes through
    `operator=`."""
    # A ctor member-init `f(v)`.
    DIRECT_INIT = auto()
    # `this->f = v` / `recv.f = v`.
    ASSIGN = auto()
    PASS = auto()
    RETURN = auto()


class SlotHolds(Enum):
    """What the slot holds, and so which conversions reach it -- the
    HOLDING part of the slot (`Slot.holds`)."""
    # Owned storage of the slot type: a record, a container, an Optional,
    # a union, a tuple, an open `T` -- a borrow converts into it, a movable
    # owned source moves in.
    OWNS = auto()
    # A plain value whose own construction / assignment takes the source
    # as it renders: a scalar, an enum, a `Ptr`, a callable, a span, a
    # value-repr Optional scalar, an owned str or bytes buffer.
    VALUE = auto()
    # A type-erased box (`Any`) the source's erasing coerce already built.
    ERASED = auto()
    # A slot no conversion is known for: only an owned value relocated whole
    # reaches it.
    RELOCATE = auto()
    # A borrow: `T&` bound once / `T*` reseatable -- a reference return, a
    # pointer-repr Optional / pointer-variant return.
    BORROWS = auto()
    POINTER = auto()
    # An open `T` whose instantiation decides by value or by reference
    # (`val_or_ref_t<T>`, `Representation.TRAIT`).
    TRAIT = auto()


class SlotPlacement(Enum):
    """Whether the region being lowered has a statement a hoisted
    declaration can precede (`_LowerCtx.placement_scope`).

    Two separate facts decide a temporary. The GRANT (`_ExprUse.allow_temps`)
    is the sink's: whether this position takes the temporary path at all,
    and it is all the admission rows read. The PLACEMENT is the region's:
    whether a statement exists to put the declaration before. It is a
    property of the whole region, not of one operand, so the scope carries
    it to every nested operand and to the rows that lower without passing
    through `_lower_expr`. Under NO_FLUSH_POINT, creating a node that
    hoists a declaration raises a `no_flush`-marked reject (`_hoisted`),
    which a member-init answers by demoting to the ctor body."""
    # The consumer sits in a statement: a hoisted decl lands before it.
    STATEMENT = auto()
    # A ctor member-init, a base-init argument or a lambda body: no
    # statement exists to host a decl.
    NO_FLUSH_POINT = auto()


@dataclass(frozen=True, slots=True)
class Slot:
    """The place a value lands in -- everything `convert` may know about
    the DESTINATION. Distinct from `_ExprUse.slot_target`, the type a
    LITERAL renders against, which can be another type (an `Optional[C]`
    field's literal renders against `C`) or none at all."""
    type: TpyType
    construct: SlotConstruct
    holds: SlotHolds = SlotHolds.OWNS
    # The const-ness the binding ASKS for (a `const T&` local, a readonly
    # parameter); a field asks for none.
    const: bool = False
    # Whether a temporary may be hoisted before the statement here -- the
    # REGION's grant (`_LowerCtx.placement`), carried as the answer.
    placement: SlotPlacement = SlotPlacement.STATEMENT
    lifetime: SlotLifetime = SlotLifetime.NONE


def storage_type(t: 'TpyType | None') -> 'TpyType | None':
    """A type as a storage slot sees it: the transparent wrappers and `Own`
    peeled, also under an Optional (`Own[T] | None` stores the same
    `std::optional<T>` a `T | None` field holds) and on tuple elements."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(t.inner)
        if isinstance(inner, OwnType):
            t = replace(t, inner=unwrap_readonly(inner.wrapped))
    if isinstance(t, TupleType) and any(
            isinstance(unwrap_readonly(e), OwnType) for e in t.element_types):
        elems = [unwrap_readonly(e) for e in t.element_types]
        t = replace(t, element_types=tuple(
            unwrap_readonly(e.wrapped) if isinstance(e, OwnType) else e
            for e in elems))
    return t


def slot_lifetime(slot: 'TpyType | None') -> SlotLifetime:
    """The lifetime a slot of this type obliges its source to
    (`holds_borrowing_view`)."""
    return (SlotLifetime.OUTLIVES_STATEMENT if holds_borrowing_view(slot)
            else SlotLifetime.NONE)


@dataclass(frozen=True, slots=True)
class _ExprUse:
    """How the immediate consumer will use one lowered expression result.

    A use names its SINK -- one of the `SinkPos` members -- and the FORM
    verdict it wants there. The sink's default verdict is its `_POS_FORMS`
    row, so most uses name only the sink; `forms=` at a site is a verdict
    the sink's row does not carry, and the number of sites needing one
    measures how much of the verdict the sink cannot yet derive from its
    own slot type. A new sink is a new member with a default row, never a
    new boolean beside the pair.

    Beside the pair, a use carries the SLOT CONTRACT -- what the slot
    asks of its source, decided from the slot, never from the source's
    spelling. Three separate parts:

      * DESTINATION (`dest`): the place the value lands in (`Slot`). None
        where the site has not stated it yet.
      * LIFETIME (`lifetime`): what the source's storage must outlive
        (`slot_lifetime`).
      * GRANT (`allow_temps`): whether this expression may take the
        temporary path; it applies only to the expression passed to
        `_lower_expr`. Whether a statement exists to host the temporary
        is the region's placement, not the use's (see `SlotPlacement`).

    What the SOURCE brings (last use, movability, form) rides on the
    lowered node, not here. `pos` stays the name diagnostics use.
    """
    result: _ExprResultUse = _ExprResultUse.VALUE
    allow_temps: bool = False
    record_ctor: _RecordCtorUse = _RecordCtorUse.DIRECT
    # WHERE this expression lands. UNSPECIFIED by default because `use` is
    # not propagated into subexpressions: a real sink as the default would
    # ride the recursive `_lower_expr` calls into deeper sinks that are
    # not it.
    pos: SinkPos = SinkPos.UNSPECIFIED
    # The verdict this site wants, when it is not the sink's default row --
    # a ptr-Optional field write lifts through `ptr_to_optional` and a
    # union one through `to_value_variant`, and both are the same sink, so
    # the SLOT TYPE and not the sink picks. None takes the sink's row.
    forms: 'frozenset[SinkForm] | None' = None
    # The SLOT this expression renders into -- the annotated decl / param /
    # element / return type. A char slot spells `'x'`, a float32 one the `f`
    # suffix, a fixed-int one the checked operator template; a both-literal
    # sub-binop renders the FULL operator there rather than folding, which
    # is why the resolved-binop operand and fixed-int arg slots name it even
    # though their own render is retyped afterwards. None is TARGET-LESS
    # (print args, compare operands, user-record method args).
    slot_target: 'TpyType | None' = None
    dest: 'Slot | None' = None
    lifetime: SlotLifetime = SlotLifetime.NONE

    def admits(self, form: SinkForm) -> bool:
        """Whether this sink admits `form`. The one spelling every
        admission ladder reads, so no consumer has to know where a verdict
        is stored -- nor re-derive it from the sink's shape."""
        forms = self.forms
        return form in (_POS_FORMS[self.pos] if forms is None else forms)


def opt_callable_slot(t: 'TpyType | None') -> bool:
    """An `Optional[Callable]` slot (`std::optional<std::function>` by
    value): its operator= absorbs the bare callable-name render a plain
    Callable slot takes."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    return (isinstance(u, OptionalType)
            and _callable_value(unwrap_readonly(u.inner)))


class FieldSlot(Enum):
    """What a FIELD of a given type stores, which decides the field write's
    family and the slot's `SlotHolds` -- read off the slot type once
    (`field_slot_class`)."""
    # A plain value whose construction / assignment takes the source as it
    # renders: a scalar, a char, an enum, a `Ptr`, a callable (or its
    # Optional), a span.
    PLAIN = auto()
    # A value-repr `Optional[scalar]`.
    VALUE_OPT = auto()
    # The str family's value (`str`, `StrView`, `String`).
    OWNED_STR = auto()
    # An owned bytes buffer: the bytes family member passed as a view of
    # its own storage (`has_view_param_form`).
    OWNED_BYTES = auto()
    # Owned storage a borrow converts into: a record or container, an
    # Optional over one (or over a type parameter, an owned str / bytes, a
    # value tuple), a recursive-alias wrapper, a stored union or tuple.
    STORAGE = auto()
    # A type-erased box (`Any`).
    ERASED = auto()
    # No family renders this slot.
    UNCLASSIFIED = auto()

    @property
    def holds(self) -> 'SlotHolds':
        return _FIELD_HOLDS[self]


_FIELD_HOLDS = {
    FieldSlot.PLAIN: SlotHolds.VALUE,
    FieldSlot.VALUE_OPT: SlotHolds.VALUE,
    FieldSlot.OWNED_STR: SlotHolds.VALUE,
    FieldSlot.OWNED_BYTES: SlotHolds.VALUE,
    FieldSlot.STORAGE: SlotHolds.OWNS,
    FieldSlot.ERASED: SlotHolds.ERASED,
    # The admission's gap: no conversion is known, so only an owned value
    # relocated whole reaches the slot (BUGS.md#field-view-escape-needs-place
    # keeps the view-family receivers out of the families that would).
    FieldSlot.UNCLASSIFIED: SlotHolds.RELOCATE,
}


def field_slot_class(t: 'TpyType | None', analyzer) -> FieldSlot:
    """The one classification of a field slot type."""
    if t is None:
        return FieldSlot.UNCLASSIFIED
    if (_eligible_scalar(t) or _eligible_char(t)
            or _eligible_enum(t, analyzer) is not None
            or _eligible_ptr_value(t, analyzer)
            or _callable_value(t) or opt_callable_slot(t) or _span_value(t)):
        return FieldSlot.PLAIN
    if _value_opt_scalar(t, analyzer) is not None:
        return FieldSlot.VALUE_OPT
    if _resolved_str_value(t, analyzer) is not None:
        return FieldSlot.OWNED_STR
    b = _resolved_bytes_value(t, analyzer)
    if b is not None and has_view_param_form(b):
        return FieldSlot.OWNED_BYTES
    if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))),
                  AnyType):
        return FieldSlot.ERASED
    if record_like(t, analyzer):
        return FieldSlot.STORAGE
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(t.inner)
        if (record_like(inner, analyzer)
                or isinstance(inner, TypeParamRef)
                or _value_opt_owned_view(t, analyzer) is not None
                or _value_tuple(inner, analyzer) is not None):
            return FieldSlot.STORAGE
    if (isinstance(t, RecursiveAliasInstanceType)
            or _storage_copy_value(t, analyzer)):
        return FieldSlot.STORAGE
    return FieldSlot.UNCLASSIFIED


def field_slot_use(slot: 'TpyType | None', construct: SlotConstruct,
                   analyzer, placement: SlotPlacement) -> _ExprUse:
    """The slot a record FIELD hands its source, decided once per statement
    from the declared slot -- a field write assigns, a member-init
    direct-initializes. A field holds storage or a plain value, never a
    borrow; its lifetime is the slot type's. It grants its source the
    temporary path wherever it sits (grant vs placement: `SlotPlacement`).
    Each site then names its sink and verdict with `dataclasses.replace`,
    keeping the slot."""
    lifetime = slot_lifetime(slot)
    return _ExprUse(
        dest=(Slot(slot, construct,
                   holds=field_slot_class(slot, analyzer).holds,
                   placement=placement, lifetime=lifetime)
              if slot is not None else None),
        lifetime=lifetime, allow_temps=True)


def _return_holds(rt: TpyType, getter: bool, analyzer) -> SlotHolds:
    """How a function's result is handed back, as its signature spells it
    (`return_representation`): a reference or a pointer into storage the
    callee does not own, an open `T` the instantiation decides, or a value
    the slot owns. A property getter of a pointer-repr shape hands back the
    field's storage by reference instead
    (`property_getter_returns_storage_ref`)."""
    if getter and property_getter_returns_storage_ref(rt):
        return SlotHolds.BORROWS
    bare = unwrap_readonly(unwrap_send_sync(rt))
    rep = return_representation(rt)
    if rep is Representation.TRAIT:
        return (SlotHolds.OWNS if isinstance(bare, OwnType)
                else SlotHolds.TRAIT)
    if rep is Representation.REFERENCE:
        u = unwrap_ref_type(bare)
        return (SlotHolds.POINTER
                if isinstance(u, (OptionalType, UnionType))
                and u.uses_pointer_repr() else SlotHolds.BORROWS)
    if rep is Representation.VIEW:
        return SlotHolds.VALUE
    st = storage_type(rt)
    cls = field_slot_class(st, analyzer)
    if cls in (FieldSlot.STORAGE, FieldSlot.UNCLASSIFIED):
        # The return builds its own result whatever the type: a value
        # type's is a copy the construction takes as the source renders.
        return SlotHolds.VALUE if st.is_value_type() else SlotHolds.OWNS
    return cls.holds


def return_slot(lc: '_LowerCtx') -> 'Slot | None':
    """The slot a `return` in this body hands its value to, decided once per
    body from the declared result type (`_Prescan.ret_type`, a per-@overload
    stub's when one is lowered); None for a body with no result."""
    pre = lc.prescan
    if pre.ret_slot is not None or pre.ret_type is None:
        return pre.ret_slot
    rt = pre.ret_type
    if isinstance(unwrap_readonly(unwrap_ref_type(rt)), VoidType):
        return None
    getter = bool(getattr(lc.func, "is_property_getter", False))
    # Cached per body, so it carries nothing per region: the placement a
    # nested region grants is the caller's to read where it lowers.
    pre.ret_slot = Slot(
        storage_type(rt), SlotConstruct.RETURN,
        holds=_return_holds(rt, getter, lc.analyzer),
        const=isinstance(unwrap_send_sync(rt), ReadonlyType),
        lifetime=slot_lifetime(rt))
    return pre.ret_slot


def narrow_alias_taken(bound_names, frame_field_names, *,
                       frame_self: bool, module_globals) -> 'frozenset[str]':
    """The body-wide half of the names a synthesized narrowing alias must not
    take: every binding the body makes (params, locals declared anywhere --
    including inside the very branch the alias opens -- `for`/`with` targets,
    walrus targets, comprehension, `except as`, `match` capture and lambda
    params, nested-def names), a resumable frame's fields, and the module's
    own globals (a body that only READS one never binds it, so no body scan
    can see it).

    Body-wide rather than in-scope: an alias declared at branch entry stays
    live to the closing brace, so a same-spelled binding LATER in that scope
    is a redeclaration and one in an inner scope (a comprehension's loop var)
    shadows the alias for the reads inside it.

    Shared with `lower_resumable`'s admission scan, which runs before a
    `_LowerCtx` exists."""
    taken = frozenset(bound_names) | frozenset(frame_field_names)
    if frame_self:
        # A resumable frame stores its receiver as the reference `__self`,
        # which is not one of its FIELDS -- so nothing above sees it, and a
        # fieldless frame would report nothing at all. A plain method's
        # receiver is `this`, which is no name an alias can take.
        taken = taken | {"__self"}
    return taken | frozenset(module_globals)


# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _ParamFacts(NamedTuple):
    """The param-keyed name sets of `_Prescan`, derived from a signature's
    `(name, type)` pairs. Field names match the `_Prescan` attributes."""
    # Param names, for checks that must tell a param from a local (a str
    # param's aug-assign would need the owned-copy prologue -- see
    # _str_aug_append_ok).
    param_names: set[str]
    # Own[str]/Own[bytes] params: the signature spells the OWNED type by
    # value, so their name reads are STORAGE -- carved out of the
    # param-implies-view verdicts (`_str_name_form`/`_bytes_name_form`).
    owned_viewfam_params: set[str]
    # `Own[tuple[...]]` params: the signature binds the STORAGE tuple by
    # value, so an element read is a `T&`/value -- never the borrow
    # param's deref-flagged `(*std::get<i>(p))` (the alias-decl
    # predicate keys on this; the expr type strips Own and cannot tell).
    own_tuple_params: set[str]
    # Value-repr Optional[cheap scalar] params (`int32 | None`): a
    # `return <param>` into a value-optional return slot passes the WHOLE
    # optional bare (deref-on-narrow stripped), so return lowering keys on
    # this to admit a narrowed param name the generic tail would deref.
    value_opt_params: set[str]


def _param_facts(params, analyzer) -> _ParamFacts:
    return _ParamFacts(
        param_names={n for n, _t in params},
        owned_viewfam_params={
            n for n, t in params if _own_viewfam_param(t) is not None},
        own_tuple_params={
            n for n, t in params
            if isinstance((_otp := unwrap_readonly(unwrap_send_sync(t))),
                          OwnType)
            and isinstance(unwrap_readonly(_otp.wrapped), TupleType)},
        value_opt_params={
            n for n, t in params
            if _value_opt_scalar(t, analyzer) is not None},
    )


class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "nonlocal_names",
                 "hoisted", "move_through",
                 "alias_sources", "alias_born", "owned_viewfam_params",
                 "ret_storage_opt", "ret_ptr_opt", "ret_borrow_tuple",
                 "ret_nullable_tuple",
                 "ret_record_borrow", "ret_record_storage",
                 "ret_res_container",
                 "ret_value_tuple", "ret_generic_tuple",
                 "ret_own_storage_tuple", "ret_wrapper_ref_tuple",
                 "ret_str", "ret_bytes",
                 "ret_char", "ret_union", "ret_ptr_union", "ret_union_borrow",
                 "ret_own_union",
                 "ret_genrec", "ret_own_wrapper", "ret_wrapper_borrow",
                 "ret_dyn_borrow", "ret_dyn_own",
                 "ret_supported", "ret_callable",
                 "ret_value_opt", "ret_value_opt_view",
                 "ret_value_opt_tuple", "ret_type", "ret_slot",
                 "value_opt_params", "param_names", "bound_names",
                 "module_global_names",
                 "own_tuple_params",
                 "has_self", "is_constructor", "global_seeded", "global_readonly",
                 "global_cpp", "global_write_cpp", "native_globals",
                 "global_slots")

    def __init__(self, func: TpyFunction, analyzer,
                 params_override=None, return_type_override=None,
                 scan_override=None, hoisted_override=None,
                 move_through_override=None) -> None:
        # `params_override` / `return_type_override` carry a per-@overload
        # STUB's signature: the impl body is emitted against the stub's
        # types, so every signature-derived fact here must key on them.
        src_params = func.params if params_override is None else params_override
        (self.param_names, self.owned_viewfam_params, self.own_tuple_params,
         self.value_opt_params) = _param_facts(src_params, analyzer)
        # Whether the callable has a `self` receiver (instance method) -- the
        # lowering arms that treat the name `self` specially (the return-self arm,
        # the self-rebind rejects) key on this so a free function's local or
        # param that merely SHARES the name is not misclassified.
        self.has_self = bool(func.is_method and not func.is_staticmethod)
        # A constructor body: field writes here interact with the ctor MIL /
        # non-default-constructible-field emit (a separate deletion target),
        # so the plain-record field-write rung stays a method/function-body
        # shape and rejects in this position.
        self.is_constructor = bool(func.is_method and func.name == "__init__")
        # `global`-declared names lower_function seeded into scope (eligible
        # same-module scalar globals); the TpyGlobal lowering arm keys on it.
        self.global_seeded: frozenset[str] = frozenset()
        # Same-module value globals seeded READ-ONLY (never assigned in this
        # body -- see _seed_readonly_globals); the name-read witness keys on it.
        self.global_readonly: frozenset[str] = frozenset()
        # Read-only-seeded native/imported value globals: name -> the
        # PRE-RENDERED spelling THIRName.cpp carries (qualify_native_name /
        # imported_variable_cpp). Disjoint from global_readonly (those
        # render bare).
        self.global_cpp: dict[str, str] = {}
        # WRITE-seeded native-linkage globals: name -> the BARE C-name
        # target spelling the global-write arm uses
        # (`native_global_names.get(name, name)`, unqualified).
        self.global_write_cpp: dict[str, str] = {}
        # POINTER-SLOT globals seeded read-only (non-value record/container
        # `T* g{};` slots): also a pointer record, so reads take the slot
        # renders (`(*g)`, `g->`, `&(*g)`); imported ones carry their
        # qualified spelling in global_cpp.
        self.global_slots: frozenset[str] = frozenset()
        # Module native-linkage globals (name -> C/C++ symbol,
        # module_native_global_names; lower_function threads them through);
        # the try hoist arm rejects a colliding predecl name, and the
        # spelled-read witness keys native vs imported on membership.
        self.native_globals: 'Mapping[str, str] | frozenset[str]' = frozenset()
        # The module-init walk has no per-function scan entry -- its facts come
        # from the analyzer's top_level_* results, passed in as overrides
        # (gen_module_init seeds ctx from exactly those three).
        scan = (analyzer.function_scan_results.get(func)
                if scan_override is None else scan_override)
        global_decls = analyzer.function_global_decls.get(func, set())
        # Every name the body binds anywhere, params included -- the set a
        # SYNTHESIZED local (a narrowing extraction alias) must stay clear of.
        # Body-wide on purpose: `declared` at the site only holds the bindings
        # made BEFORE it, and a later same-spelled declaration collides just
        # as hard.
        self.bound_names: frozenset[str] = frozenset(
            self.param_names | (scan.bound_names() if scan else set()))
        # Module-scope names (globals, functions, records, imports): a body
        # that only READS one binds nothing, so `bound_names` cannot see it,
        # yet a synthesized local spelled the same hides it for the rest of
        # the scope.
        self.module_global_names: frozenset[str] = frozenset(
            analyzer.global_ns.all_bindings())
        self.reassigned = (scan.reassigned - global_decls) if scan else set()
        # F2d: the subset reassigned with an rvalue source (the rebind-slot
        # trigger -- mirrors codegen's `ctx.rvalue_reassigned_vars` seeding).
        self.rvalue_reassigned = (
            (scan.rvalue_reassigned - global_decls) if scan else set())
        # Names a NESTED def rebinds through `nonlocal` -- set by the
        # nested-def lowering scope; the enclosing function's storage, so
        # no reseat inside the lambda may take a slot of its own.
        self.nonlocal_names: set[str] = set()
        self.hoisted = (analyzer.function_hoisted_vars.get(func, set())
                        if hoisted_override is None else hoisted_override)
        self.move_through = (
            analyzer.function_move_through_vars.get(func, set())
            if move_through_override is None else move_through_override)
        # Alias facts for the `del x` move-sink skips: names some alias binds
        # to (codegen's `ctx.aliased_vars` -- moving from them would gut the
        # alias) and names whose FIRST binding was an alias (`ctx.alias_names`
        # -- such a pointer-local may point at another local's storage).
        self.alias_sources = set(scan.alias_sources.values()) if scan else set()
        self.alias_born = scan.initial_alias_names if scan else set()
        # F2c: the function's storage-form Optional[F1-record] return slot, if any
        # (`Own[T] | None` -> `std::optional<T>`), so a `return None` /
        # `return <borrow T*>` lowers to `std::nullopt` / `ptr_to_optional`. None
        # for every other return type (the value-scalar/pointer-repr paths).
        rt = (return_type_override
              if return_type_override is not None
              else (func.return_type
                    if isinstance(func.return_type, TpyType) else None))
        self.ret_type: TpyType | None = rt
        # `return_slot` fills it on first use.
        self.ret_slot: 'Slot | None' = None
        self.ret_storage_opt = _storage_optional_return_wide(rt, analyzer)
        # The pointer-repr Optional return slot over the WIDE pointee class
        # (`A | None` / `T | None` / `W | None` -> a borrow `A*` returned by
        # value): `None` -> `nullptr`, an already-pointer name -> bare, a
        # pointee-typed name -> `&(name)` (_optional_pointer_form_value's
        # admitted subset; every render is pointee-shape-blind).
        self.ret_ptr_opt = _optional_ptr_borrow_wide(rt, analyzer)
        # The value-repr Optional[cheap scalar] or Optional[value record]
        # return slot (`-> int32 | None` / `-> Coord | None` ->
        # `std::optional<T>`): `return None` -> `std::nullopt`, a value-opt
        # name passes the whole optional bare, every other source rides the
        # generic return tail (type-exact or coerce-wrapped).
        self.ret_value_opt = _value_opt_scalar(rt, analyzer)
        if self.ret_value_opt is None:
            self.ret_value_opt = _value_opt_record(rt)
        if self.ret_value_opt is None and rt is not None:
            # A value-BOUND `Optional[T]` return in a generic body
            # (`-> T | None` under `T: ValueType` -> `std::optional<T>`)
            # renders like the scalar family: `return None` -> nullopt,
            # a member/field source rides the generic tail's implicit
            # optional conversion. Return-slot only -- bindings/args keep
            # the scalar-keyed classification.
            _rvb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
            if (isinstance(_rvb, OptionalType)
                    and not _rvb.uses_pointer_repr()
                    and _is_type_param_slot(unwrap_readonly(_rvb.inner))):
                self.ret_value_opt = _rvb
        # The value-repr Optional[view] return slot -- str (`-> str | None` ->
        # `std::optional<std::string>`) OR bytes (`-> bytes | None` ->
        # `std::optional<std::vector<uint8_t>>`): `return None` -> `std::nullopt`,
        # a value-repr Optional[view] param name takes the view->owned shim
        # (`x ? std::make_optional(<conv>(*x)) : std::nullopt`, THIROptViewArg;
        # `<conv>` = `std::string` / `::tpy::Bytes`), and a str/bytes literal
        # lands bare (the owned literal / implicit conversion). The owned-view
        # twin of ret_value_opt.
        self.ret_value_opt_view = _value_opt_view(rt, analyzer)
        # The value-repr Optional[value tuple] return slot (`-> tuple[float,
        # int32] | None` -> `std::optional<std::tuple<...>>`): `return None`
        # -> `std::nullopt`, a tuple literal spells the inner tuple's
        # brace-init, an un-narrowed value-tuple name passes bare (the
        # optional's converting ctor absorbs both). The tuple twin of
        # ret_value_opt.
        self.ret_value_opt_tuple = _value_opt_tuple(rt, analyzer)
        # F3: the function's borrow-form pointer-repr tuple return slot, if any
        # (`tuple[..., Ref]` -> `std::tuple<..., T*>`), so a `return <storage tuple
        # lvalue>` lifts via `tuple_to_pointer`. None for every other return type.
        # A nullable reference-tuple return keeps the tuple's return layout
        # inside the optional (`std::optional<std::tuple<Box*, int32_t>>`),
        # so the bare twin's fact over the inner renders the non-None value
        # (the optional absorbs it) and `return None` is the empty optional.
        self.ret_nullable_tuple: 'OptionalType | None' = (
            unwrap_readonly(unwrap_send_sync(rt))
            if _optional_borrow_tuple(rt, analyzer) is not None else None)
        self.ret_borrow_tuple = _borrow_tuple_return_type(
            self.ret_nullable_tuple.inner
            if self.ret_nullable_tuple is not None else rt, analyzer)
        # The borrow-form REFERENCE return slot (`-> Box` -> `Box&`,
        # `-> list[T]` -> `std::vector<T>&`): a bare borrow name
        # (`return name;`), `self` (`return (*this);`), a plain field read
        # (`return recv.field;`), an element lvalue or a pointer-slot
        # global; the return-stmt arm rejects every other source shape.
        self.ret_record_borrow = _record_borrow_return(rt, analyzer)
        # The storage-form REFERENCE return slot (`-> Own[Box]` -> `Box` by
        # value, `-> Own[list[T]]` -> a by-value vector/map/set): bare names,
        # rvalue ctor / by-value calls, and the container-only literal /
        # repeat / comprehension sources return bare; every other source
        # shape rejects.
        self.ret_record_storage = _record_storage_return(rt, analyzer)
        # The RESUMABLE container return slot -- wider at the `Own` axis
        # (a coro's slot holds T by value either way); read only by the
        # resumable return arm's empty-literal guard.
        self.ret_res_container = _res_container_return(rt, analyzer)
        # The value-tuple return slot (`-> tuple[int, str]` -> a by-value
        # `std::tuple<...>`): a tuple literal renders the spelled brace-init
        # (THIRTupleLiteral) recursively -- the return element set is widened
        # over the narrow `_value_tuple` (nested value-tuple / value-Optional[
        # scalar] elements). A bare value-tuple name return stays on the narrow
        # arm (no bare-copy read arm for a widened-element receiver).
        # The union-element widening: a VALUE tuple with a value-union
        # element returns the same spelled brace-init (the variant
        # element's converting ctor absorbs the member render).
        self.ret_value_tuple = (_value_tuple_return(rt, analyzer)
                                or _union_elem_value_tuple(rt, analyzer))
        # The GENERIC tuple return slot (>=1 TypeParamRef element): the
        # RESUMABLE return arm's `val_or_ptr_t` bridge AND the sync return
        # arm's literal row (`return (tag, val)` -> the spelled brace-init
        # with per-element `to_val_or_ptr` wraps) both consume it.
        self.ret_generic_tuple = _generic_value_tuple_return(rt, analyzer)
        # The Own[tuple] STORAGE return slot with a non-value member
        # (`-> Own[tuple[str, Resource]]`): a tuple LITERAL of storage-direct
        # members returns the spelled brace-init.
        self.ret_own_storage_tuple = _own_storage_tuple_return(rt, analyzer)
        # The REFERENCE-element tuple return slot (`-> tuple[Tree, int32]`
        # -> `std::tuple<Tree&, int32_t>`): literal-of-lvalue-names sources
        # only, gated at the return arm.
        self.ret_wrapper_ref_tuple = _wrapper_ref_tuple_return(rt, analyzer)
        # A view source returned into an owned str/bytes slot must copy.
        self.ret_str, self.ret_bytes = _viewfam_return_slots(rt, analyzer)
        # S4: a char return slot -- `return "x"` needs a target-typed char
        # literal (`'x'`), a shape the return arm rejects.
        self.ret_char = _eligible_char(rt)
        # F4 U1: a value-union return slot -- `return None` renders
        # `std::monostate{}` (target-typed); other sources return bare.
        self.ret_union = _eligible_value_union(rt)
        # A PROPERTY GETTER's ptr-variant union return renders the STORAGE
        # variant by reference (`std::variant<Circle, Square>&` -- the
        # signature layer's is_property_getter arm), NOT the ptr-variant
        # convention -- so this fact is derived first and excludes the
        # flavor from ret_ptr_union below. A bare same-union self-FIELD
        # returns bare (`return this->_shape;`).
        self.ret_union_borrow = (
            _eligible_ptr_union(rt, analyzer)
            if getattr(func, "is_property_getter", False) else None)
        # F4 U2: a pointer-variant return slot -- only same-union borrow
        # names return bare; a MEMBER record name needs an `&(...)`
        # address-of lift, which this slice does not render.
        self.ret_ptr_union = (None if self.ret_union_borrow is not None
                              else _eligible_ptr_union(rt, analyzer))
        # An `Own[A | B]` record-member union return slot (a by-value
        # storage `std::variant<A, B>`): a member-record ctor rvalue
        # returns bare (the converting ctor absorbs it).
        self.ret_own_union = _own_storage_union_return(rt, analyzer)
        # The generic-instance sibling: an `Own[Tree[int32]]` slot returns
        # the wrapper struct by value; source rows gate at the return arm.
        self.ret_genrec = _own_genrec_return(rt)
        # The non-generic wrapper-union sibling (`-> Own[V]` -> `V` by
        # value): None -> monostate, scalar literals bare, container
        # literals via the ru render, member container names bare.
        self.ret_own_wrapper = _own_wrapper_return(rt, analyzer)
        # The wrapper BORROW return slot (`-> Expr` -> `Expr&`): NAME
        # sources only, gated at the return arm.
        self.ret_wrapper_borrow = _wrapper_borrow_return(rt, analyzer)
        # The @dynamic-protocol BORROW return slot (`-> P` -> `P&`,
        # `-> readonly[P]` -> `const P&`): NAME sources only (a borrow
        # param bare, a pointer-local/global deref) -- the return arm gates.
        self.ret_dyn_borrow = _dyn_borrow_return(rt)
        # The `Own[@dynamic P]` return slot (`std::unique_ptr<P>`):
        # verdict-keyed source rows at the return arm ('forward' names and
        # Own[P]-returning calls bare, conformer ctor rvalues wrapped).
        self.ret_dyn_own = _own_dyn_return(rt)
        # A value-bearing return must select one of the representations the
        # return arm consumes. Signatures are emitted by the skeleton layer;
        # this fact is checked only when lowering reaches an actual return
        # value.
        # A non-template Callable return slot (`std::function<...>` by
        # value): the only admitted source is a closure local's bare name
        # (`return add;` -- the lambda converts implicitly).
        self.ret_callable = bool(
            isinstance(rt, CallableType) and not rt.is_template)
        # `Own[T]` on a VALUE scalar is a no-op spelling (resolves to plain
        # T -- `auto_own[int32]` returns `int32_t`); unwrap for the scalar
        # rows only, the non-value Own families keep their own fields.
        own_v = (unwrap_readonly(rt.wrapped)
                 if isinstance(rt, OwnType) else None)
        own_value_scalar = (own_v is not None
                            and (_eligible_scalar(own_v)
                                 or _eligible_char(own_v)
                                 # `Own[<structural protocol>]` returns the
                                 # same `auto` slot as the bare protocol
                                 # (`auto_own[Iterator[T]]`); @dynamic Own
                                 # keeps the adapter machinery out.
                                 or _protocol_auto_slot(own_v)))
        self.ret_supported = bool(
            rt is None or isinstance(rt, VoidType)
            or self.ret_callable
            or _eligible_scalar(rt) or _eligible_char(rt)
            # A value-form record returns by value like a scalar: every
            # source shape rides the generic tail (`return c;`).
            or _value_record_slot(rt)
            or own_value_scalar
            or _is_type_param_slot(rt) or _own_type_param_slot(rt)
            or _eligible_enum(rt, analyzer) is not None
            or _eligible_ptr_value(rt, analyzer)
            or _span_return(rt)
            # A native-iterator value return (`def __iter__ -> SpanIter[T]`)
            # and a structural-protocol return (`-> Iterator[T]`, the C++
            # signature already spells the concrete/auto type): the value
            # renders bare through its own call arms.
            or _native_iter_value_slot(rt, analyzer)
            or _protocol_auto_slot(rt)
            or self.ret_storage_opt is not None
            or self.ret_ptr_opt is not None
            or self.ret_value_opt is not None
            or self.ret_value_opt_view is not None
            or self.ret_value_opt_tuple is not None
            or self.ret_borrow_tuple is not None
            or self.ret_record_borrow is not None
            or self.ret_union_borrow is not None
            or self.ret_record_storage is not None
            or self.ret_value_tuple is not None
            or self.ret_generic_tuple is not None
            or self.ret_own_storage_tuple is not None
            or self.ret_wrapper_ref_tuple is not None
            or self.ret_str is not None or self.ret_bytes is not None
            or self.ret_union is not None or self.ret_ptr_union is not None
            or self.ret_own_union is not None
            or self.ret_genrec is not None
            or self.ret_own_wrapper is not None
            or self.ret_wrapper_borrow is not None
            or self.ret_dyn_borrow is not None
            or self.ret_dyn_own is not None)

    def binds_global(self, name: str) -> bool:
        """Does `name` read as a module GLOBAL in this body? The seeding
        decides it and already excludes a sema-hoisted name -- Python
        scoping makes a name the function assigns a LOCAL, whatever the
        module binds. Asking the seeding is not the same as asking the
        module's native-global map: the NAME of a native global says
        nothing about this body's scope, and shadowing one is ordinary
        Python."""
        return (name in self.global_seeded
                or name in self.global_readonly
                or name in self.global_cpp
                or name in self.global_slots)

    def namespace_scope_names(self) -> frozenset[str]:
        """Every name the body reads at C++ NAMESPACE scope. A closure
        names these directly instead of capturing them -- capturing a
        namespace-scope object is ill-formed C++. Wider than
        `binds_global`, which answers about this body's scope: a
        write-seeded and a native-linkage global are namespace-scope
        objects too."""
        return (frozenset(self.global_readonly)
                | frozenset(self.global_slots)
                | frozenset(self.global_cpp)
                | frozenset(self.global_write_cpp)
                | frozenset(self.global_seeded)
                | frozenset(self.native_globals))


def prescan_without(prescan: _Prescan, names: AbstractSet[str]) -> _Prescan:
    """A copy of `prescan` with `names` stripped from the per-name facts an
    inner scope binding them does not inherit -- the view `shadow_scope`
    gives the lowering of that scope, for the planner."""
    out = copy.copy(prescan)
    for a in _SHADOWED_PRESCAN_FACTS:
        setattr(out, a, _without_names(getattr(prescan, a), names,
                                       shared=True))
    return out


def nested_def_prescan(func: TpyFunction, outer: _Prescan, analyzer,
                       nonlocal_names: 'AbstractSet[str]') -> _Prescan:
    """The prescan a nested def's body lowers (and plans) under: its own
    scan joined with the enclosing function's. Nested defs have no stored
    move-through facts, so outer names cannot lend them."""
    prescan = _Prescan(func, analyzer)
    # Module-level facts carry over; the nested func has no global decls
    # (gate-rejected), so the seeded-globals gating fields stay empty.
    prescan.native_globals = outer.native_globals
    prescan.global_readonly = outer.global_readonly
    prescan.global_cpp = outer.global_cpp
    # A CAPTURED outer param keeps its outer render inside the lambda (the
    # capture holds the enclosing signature's form -- a `str` param is a
    # `std::string_view` on both sides), so the form predicates that ask
    # "is this name a param" must see the outer names too.
    prescan.param_names = prescan.param_names | outer.param_names
    prescan.owned_viewfam_params = (prescan.owned_viewfam_params
                                    | outer.owned_viewfam_params)
    # The nested body's OWN rebinds join the outer sets: a local the nested
    # def declares and rebinds is a rebind-slot pointer-local like any
    # other (its rebinds carry sema's storage verdict), while a captured
    # name keeps the outer classification.
    own_scan = analyzer.function_scan_results.get(func)
    if own_scan is None:
        own_scan = scan_reassigned_vars(
            func.body, pre_declared={p for p, _ in func.params})
    prescan.reassigned = outer.reassigned | own_scan.reassigned
    prescan.rvalue_reassigned = (outer.rvalue_reassigned
                                 | own_scan.rvalue_reassigned)
    prescan.nonlocal_names = set(nonlocal_names)
    # The nested body's OWN escape hoists join the outer set for the same
    # reason its rebinds do: a local it declares and lends past a loop needs
    # its slot inside the closure body.
    prescan.hoisted = outer.hoisted | analyzer.function_hoisted_vars.get(
        func, set())
    return prescan


@dataclass
class _NarrowScope:
    """Lowering's branch/loop-scoped isinstance-narrowing state.

    `narrowed` maps each U3 isinstance-narrowed source var to its live
    extraction alias
    (`ctx.narrowed_vars`); reads rename, the isinstance condition keeps the
    original variant. `persistent_aliases` holds the names already declared at
    the enclosing C++ scope, for the post-if statement-level extraction's
    collision bump (`__v` -> `__v_2`). `subject_union` records
    each narrowed subject's ORIGINAL union after `declared` is retyped to
    the member. Consumers use it for alias lifting and re-extraction;
    subscript dispatch still follows the narrowed member type.
    `persistent_narrowed` is the
    var-level subset whose live alias is statement-level.
    A branch/loop body lowers under a `snapshot()`, restored at the closing
    brace -- scope-snapshot semantics. NB `_LowerCtx.inline_narrowed`
    stays outside the bundle: it is condition-scoped (its own snapshot in
    `_lower_narrow_cond`), never live across statements."""
    narrowed: dict[str, str] = field(default_factory=dict)
    persistent_aliases: set[str] = field(default_factory=set)
    subject_union: dict[str, UnionType] = field(default_factory=dict)
    union_layouts: dict[str, THIRUnionLayout] = field(default_factory=dict)
    persistent_narrowed: set[str] = field(default_factory=set)
    # Vars narrowed from an ANY subject (D15): consumers keyed on the
    # declared-type classification (print args) read this to pick
    # the Any fall-through render instead of the union reject.
    any_narrowed: set[str] = field(default_factory=set)
    # Vars narrowed via the polymorphic if-init cast: var -> the pre-spelled
    # C++ read (`(*__p_ptr)`), rendered verbatim through THIRName.cpp. No
    # alias statement exists -- the if-init pre-binds the cast pointer.
    spelled: dict[str, str] = field(default_factory=dict)
    # Poly post-if/assert-narrowed vars: var -> the ORIGINAL declared type
    # (the narrow retypes `declared` to the member, but every later cast in
    # a re-narrowing chain must anchor to the source decl, which narrowing
    # never retypes: the pointer-vs-`&` cast-arg spelling, the
    # adapter-vs-dynamic_cast pick, and the readonly const verdict all key
    # on it).
    poly_source: dict[str, TpyType] = field(default_factory=dict)
    # Every synthesized narrowing local currently LIVE in the enclosing scope
    # chain -- branch extraction aliases, the poly if-init cast pointers, and
    # (through `persistent_aliases`) the statement-level ones. The freshness
    # ladder consults it via `_LowerCtx.alias_taken`: two subjects whose
    # spellings collide only AFTER a bump (`a` bumped to `__a_narrowed`
    # because the body binds `__a`, and a nested narrowing of `a_narrowed`)
    # would otherwise both declare the same name, and the inner shadow makes
    # reads of the outer subject return the wrong object.
    live_aliases: set[str] = field(default_factory=set)

    def snapshot(self) -> '_NarrowScope':
        # Field-generic so a new container can't be silently shared: every
        # field is a dict/set, shallow-copied per snapshot.
        return _NarrowScope(**{f.name: getattr(self, f.name).copy()
                               for f in fields(self)})

# Every mutable per-name classification set on _LowerCtx, by scoping rule.
# The mirror of codegen's LocalScopeSnap (codegen_cpp/context.py): a branch
# body lowers over a per-branch `declared` COPY, so the lc-set entries its
# decls register must pop with the branch too -- a same-named sibling-branch
# or post-scope decl would otherwise classify against state its scope never
# saw (the missed-restore bug class).
#
# BRANCH-SCOPED: snapshotted and restored by `branch_scope()`. `narrow` joins
# the snapshot via its own `snapshot()`.
_BRANCH_SCOPED_SETS = (
    "dyn_protocol_locals", "branch_hoisted", "match_ptr_hoists",
    "forbidden_reads", "forbidden_writes",
    # A `static __global_slot_N` allocated inside a branch is scoped to that
    # branch, so the "this global already has a reusable slot" fact must pop
    # with it: a later write outside the branch (or in a sibling branch)
    # allocates its own, while a second write WITHIN the branch reuses.
    "global_slot_assigned",
    # A walrus's pre-decl lands on the named row of the statement it sits in,
    # so it is in C++ scope exactly as long as this block: a sibling branch
    # binding the same name must declare it again.
    "walrus_predeclared",
)
# DELIBERATELY NOT branch-scoped. Registration that must survive a scope
# (with-targets, match full-binds, a nested def's name) is done by ORDERING:
# the caller registers in its own frame, outside the inner push/pop.
#   unhandled_hoists -- function-scoped residue ledger; branch drains ARE the
#       accounting, restoring them would fake un-lowered hoists.
#   nested_def_locals -- the bound name outlives its block (Python names are
#       function-scoped, so it is re-added after the scope restore).
#   nested_returns -- function-scoped resumable accumulator (the seam table).
#   inline_narrowed -- condition-scoped: saved/restored by _lower_narrow_cond
#       within a single condition, never live across statements.
#   tparam_bounds -- init-only per-function fact.
#   walrus_slot_locals -- the owned-slot walrus targets: the decl is
#       function-scoped (named row) and the binding outlives its branch
#       (Python scoping), so reads after the branch keep the `(*n)` render.
#   frame_local_types -- the frame's resolved slot types, seeded once by
#       lower_resumable (the frame ctx's var_types); a frame field's
#       slot is one type for the whole body, position-blind.
#   frame_field_names -- the frame's member set, seeded once by
#       lower_resumable; membership is a layout fact, not a scope one.
_FUNCTION_SCOPED_STATE = (
    "unhandled_hoists", "nested_def_locals", "nested_returns",
    "inline_narrowed", "tparam_bounds",
    "walrus_slot_locals",
    "frame_local_types",
    "frame_field_names",
    # Forwarded proto-param aliases (`xs = it` in a resumable): a
    # compile-time name->param map seeded once at entry, never mutated.
    "forwarded_map",
    # Per-@overload-stub literal facts: seeded once at lower_function setup
    # for a literal-only group's stub, immutable across the walk.
    "overload_literal_facts",
    # Module-init facts: seeded once from the module's globals / import list
    # and never branch-scoped -- a global's slot identity and an import's
    # chain do not change inside a branch.
    "global_ptr_slots", "import_calls",
    "pre_decl_import_cpp",
    # The memoized body-wide half of `alias_taken`: keyed on the state it
    # reads, so a branch cannot make it stale.
    "_alias_taken_memo",
)

# Every per-name classification an inner Python scope (a nested def, a
# lambda) would otherwise inherit from the enclosing body: `shadow_scope`
# strips the names the inner scope binds from each. The branch-scoped sets,
# plus the frame layout, walrus, alias and sema-ownership facts that are
# function-scoped for the ENCLOSING body. The lazily computed borrow-tuple
# const sets and the alias memo stay out: a restore would discard a result
# computed inside the scope while its computed-flag survives.
_SHADOWED_LC_STATE = _BRANCH_SCOPED_SETS + (
    "nested_def_locals", "inline_narrowed",
    "walrus_slot_locals", "sema_movable_locals",
    "deref_view_spelled",
    "forwarded_map", "literal_facts", "global_ptr_slots",
    "frame_local_types", "frame_field_names", "frame_own_tuple_types",
    "plain_frame_fields", "borrow_tuple_frame_locals", "coro_handle_slots",
    "value_tuple_frame_locals", "opt_tuple_holders", "opt_ptr_frame_locals",
    "rebind_ptr_frame_locals", "oneshot_lift_locals", "alias_ptr_locals",
    "const_alias_ptr_locals", "const_frame_bindings", "unpack_ptr_targets",
    "source_typed_holders",
    # An enclosing `@overload` stub's facts about ITS params: an inner param
    # of the same spelling must not fold against them.
    "overload_narrowing", "overload_literal_facts",
    # A module's import spelling for a name it redefines later: an inner
    # binding of that name is the inner local, never the import.
    "pre_decl_import_cpp",
)
# The `_Prescan` twin: the enclosing signature's param facts, its rebind /
# hoist / alias sets, and every seeded-global table. `bound_names` and
# `module_global_names` stay: they fence SYNTHESIZED names, which must avoid
# the shadowed spellings too.
_SHADOWED_PRESCAN_FACTS = _ParamFacts._fields + (
    "reassigned", "rvalue_reassigned", "hoisted", "move_through",
    "alias_sources", "alias_born",
    "global_seeded", "global_readonly", "global_cpp", "global_write_cpp",
    "global_slots", "native_globals",
)
# `_NarrowScope` fields keyed by a narrowed subject's name; the alias-name
# registries (`live_aliases`, `persistent_aliases`) stay, for the same
# reason `bound_names` does.
_SHADOWED_NARROW_FACTS = ("narrowed", "subject_union", "union_layouts",
                          "persistent_narrowed", "any_narrowed", "spelled",
                          "poly_source")

# Every other field of the three owners, so each is classified exactly once
# (tpyc/thir/test_shadow_scope_fields.py holds the partition): a name-keyed
# field left out of the shadowed sets leaks the enclosing body's facts into a
# nested def or lambda that rebinds the name.
#
# Shadowed by `shadow_scope` itself rather than through a table.
_SHADOWED_LC_BY_HAND = ("params", "narrow", "self_receiver", "member_self")
_LC_NOT_NAME_KEYED = (
    "analyzer", "func", "prescan", "render_type", "render_type_stored",
    "render_resolve", "render_concept", "record_name", "self_cpp",
    "self_is_pointer", "error_return_cpp", "overload_stub_return",
    "overload_terminated", "resumable_leaf_mode", "placement",
    "slot_hoist_ok", "in_container_elem", "in_finally_helper", "in_for_body",
    "top_level_scope", "global_binding_scope", "top_level_line",
    # Keyed by statement id / a list of return nodes / enclosing functions.
    "import_calls", "nested_returns", "capture_funcs",
    # The binding table's shadow check: the table models inner scopes itself.
    "table", "cursor", "shadow_report", "move_region",
)
_LC_INHERITED_BY_DESIGN = (
    # A lazily computed body-wide memo: a restore would discard a result
    # computed inside the scope.
    "_alias_taken_memo",
    # Per-function residue ledger: `_nested_def_state_scope` swaps in the
    # nested def's own, and a lambda body hoists nothing.
    "unhandled_hoists",
    # Type-param names, not value names; a generic nested def rejects.
    "tparam_bounds",
    # Node-keyed closure sites plus the outer body's entry locals, read only
    # at a top-level closure (inner ones are ineligible), outside its scope.
    "capture_sites",
)
_PRESCAN_NOT_NAME_KEYED = (
    # Return-slot facts: a nested def swaps in its own prescan and a lambda
    # body has no `return`.
    "ret_storage_opt", "ret_ptr_opt", "ret_borrow_tuple",
    "ret_nullable_tuple", "ret_record_borrow",
    "ret_record_storage", "ret_res_container", "ret_value_tuple",
    "ret_generic_tuple", "ret_own_storage_tuple", "ret_wrapper_ref_tuple",
    "ret_str", "ret_bytes", "ret_char", "ret_union", "ret_ptr_union",
    "ret_union_borrow", "ret_own_union", "ret_genrec", "ret_own_wrapper",
    "ret_wrapper_borrow", "ret_dyn_borrow", "ret_dyn_own", "ret_supported",
    "ret_callable", "ret_value_opt", "ret_value_opt_view",
    "ret_value_opt_tuple", "ret_type", "ret_slot",
    # Facts about the enclosing FUNCTION, not a name: a nested def swaps in
    # its own prescan, and a lambda parameter spelled `self` is shadowed
    # through `self_receiver` / `member_self` instead.
    "has_self", "is_constructor",
)
_PRESCAN_INHERITED_BY_DESIGN = (
    # Fences for SYNTHESIZED names, which must avoid shadowed spellings too.
    "bound_names", "module_global_names",
    # Replaced per nested def by `_nested_def_state_scope`; a lambda cannot
    # declare `nonlocal`.
    "nonlocal_names",
)
_NARROW_NOT_NAME_KEYED: tuple[str, ...] = ()
# Alias-name registries: they fence synthesized names, like `bound_names`.
_NARROW_INHERITED_BY_DESIGN = ("live_aliases", "persistent_aliases")


def _without_names(value: AbstractSet[str] | Mapping[str, object] | None,
                   names: AbstractSet[str], *, shared: bool = False
                   ) -> AbstractSet[str] | Mapping[str, object] | None:
    """`value` minus `names`, as a new object so the restore gets the
    original back. With nothing to strip it is returned as is when the
    scope cannot change it in place: None, a frozenset, or `shared`."""
    if value is None:
        return None
    if (all(n not in value for n in names)
            and (shared or isinstance(value, frozenset))):
        return value
    if isinstance(value, Mapping):
        stripped = {k: v for k, v in value.items() if k not in names}
        rewrap = getattr(value, "rewrap", None)
        return rewrap(stripped) if rewrap is not None else stripped
    return value - names


class BindingTableError(RuntimeError):
    """The binding table's planner and the lowering disagree about a scope
    or a record: an internal error, never a user diagnostic."""

    def __init__(self, msg: str) -> None:
        super().__init__(f"binding table: {msg}")


def names_with(binding: Callable[[str], BindingRepr], fact: str
               ) -> NameFacts:
    """The `NameFacts` of one `BindingRepr` field, over `binding`'s
    records."""
    assert fact in BindingRepr.__dataclass_fields__, fact
    return NameFacts(lambda n: bool(getattr(binding(n), fact)))


class _LowerCtx:
    """Per-function lowering state threaded through `_lower_stmt`.

    `render_type` renders a decl C++ type the way codegen does
    (`TypeResolver.type_to_cpp`): it qualifies cross-module records and resolves
    the live module, which `TpyType.to_cpp()` does not, so it -- not `to_cpp()` --
    is the correct source for an F1 borrow local's cpp_type. The default
    (`to_cpp`) is for analyzer-only callers (dump / standalone lowering) that
    never hit a non-value local.

    `render_type_stored` is the STORED-form sibling (`TypeResolver.
    type_to_cpp_stored`): explicit template args on generic free-fn calls
    spell through it (val_or_ref<T> for Ref types, PendingView resolution),
    so any arm rendering that spelling must use this -- not `render_type` --
    for those slots."""
    __slots__ = ("analyzer", "func", "prescan", "render_type",
                 "render_type_stored", "render_resolve", "tparam_bounds",
                 "dyn_protocol_locals", "branch_hoisted", "match_ptr_hoists",
                 "deref_view_spelled", "forwarded_map",
                 "sema_movable_locals",
                 "params", "capture_funcs", "capture_sites",
                 "self_receiver", "self_cpp", "self_is_pointer",
                 "record_name",
                 "frame_own_tuple_types",
                 "resumable_leaf_mode", "placement", "slot_hoist_ok",
                 "in_container_elem",
                 "nested_returns", "in_finally_helper",
                 "plain_frame_fields", "borrow_tuple_frame_locals",
                 "coro_handle_slots", "frame_local_types",
                 "frame_field_names",
                 "value_tuple_frame_locals",
                 "opt_tuple_holders", "opt_ptr_frame_locals",
                 "rebind_ptr_frame_locals",
                 "oneshot_lift_locals", "alias_ptr_locals",
                 "const_alias_ptr_locals", "const_frame_bindings",
                 "unpack_ptr_targets", "source_typed_holders",
                 "unhandled_hoists", "narrow", "literal_facts",
                 "inline_narrowed", "forbidden_reads", "forbidden_writes",
                 "nested_def_locals", "error_return_cpp",
                 "walrus_predeclared", "walrus_slot_locals",
                 "overload_narrowing", "overload_stub_return",
                 "overload_literal_facts",
                 "overload_terminated", "render_concept",
                 "top_level_scope", "global_binding_scope", "global_ptr_slots", "global_slot_assigned",
                 "in_for_body",
                 "import_calls", "pre_decl_import_cpp", "top_level_line",
                 "member_self", "_alias_taken_memo",
                 "table", "cursor", "shadow_report", "move_region")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None,
                 render_type_stored=None,
                 self_cpp: str = "this",
                 self_is_pointer: bool = True,
                 render_resolve=None,
                 params_override=None,
                 return_type_override=None,
                 render_concept=None,
                 scan_override=None,
                 hoisted_override=None,
                 move_through_override=None,
                 top_level_scope: bool = False) -> None:
        self.analyzer = analyzer
        self.func = func
        self.capture_sites = CaptureSites(func, eligible=not (
            top_level_scope or func.is_async or func.is_generator or func.error_return
            or func.type_params or params_override is not None or analyzer.overload_groups.get(func)
            or func.is_staticmethod or func.is_classmethod or func.is_consuming
            or func.is_property_getter or func.is_property_setter
            or func.is_auto_own_borrowing_clone or func.is_auto_own_consuming_clone
            or func.auto_readonly_polarity is not None
            or (func.is_method and func.name != "__init__"
                and func.name.startswith("__") and func.name.endswith("__"))))
        self.global_binding_scope = not (func.is_async or func.is_generator)
        # The SIGNATURE params this body is lowered against: a per-@overload
        # stub's when one is being specialized (the body is emitted against
        # the stub's types), the impl's otherwise. Every param-type lookup
        # must read this, not `func.params` -- a stub-narrowed param is
        # concrete here while the impl declares it Optional.
        self.params = tuple(func.params if params_override is None
                            else params_override)
        self.prescan = _Prescan(func, analyzer,
                                params_override=params_override,
                                return_type_override=return_type_override,
                                scan_override=scan_override,
                                hoisted_override=hoisted_override,
                                move_through_override=move_through_override)
        # Module-init walk: names live at NAMESPACE scope (pre-declared by the
        # generator, so every write is an assignment), slot temps are `static
        # __global_slot_N`, and non-value globals are writable pointer slots --
        # the global variable model, not a function-local one.
        self.top_level_scope = top_level_scope
        # Inside a for-each BODY with an element type -- the sole
        # namespace-push site, which is what drops `static` on in-branch
        # global-slot decls (if/while/with/try bodies keep global_ns).
        self.in_for_body = False
        # Non-value module globals (`std::vector<T>* g{}` at namespace scope):
        # their initializing write emits the static-slot pair, their reads ride
        # the pointer-local arms. Empty outside the module-init walk.
        self.global_ptr_slots: set[str] = set()
        # Pointer-slot globals whose static slot has already been emitted -- a
        # SECOND rvalue write would have to reuse that slot (the
        # `rebind_slots` lookup), a render this slice does not emit.
        self.global_slot_assigned: set[str] = set()
        # Per-import `__tpy_init()` chain, keyed by statement id and resolved
        # up-front by `lower_top_level` (the dedup is order- and
        # state-dependent, so it is replayed once over the whole list rather
        # than re-derived mid-walk).
        self.import_calls: dict[int, tuple[str, ...]] = {}
        # Imported names this module redefines at top level: name ->
        # (decl_line, qualified import spelling). Reads BEFORE decl_line
        # take the import; `top_level_line` is the module-init walk's
        # position.
        self.pre_decl_import_cpp: dict[str, tuple[int, str]] = {}
        self.top_level_line: int = 0
        # Arm-scoped LiteralType facts (a Literal-subject match narrows the
        # subject per arm). The dead-branch FOLDS over these (compare /
        # membership / bool-chain) are not lowered yet, so expression
        # lowering RAISES on a fact-name compare instead of silently
        # emitting the unfolded form -- the fence at the consumer.
        self.literal_facts: dict[str, TpyType] = {}
        self.render_type = render_type or (lambda t: t.to_cpp())
        self.render_type_stored = (render_type_stored
                                   or (lambda t: t.to_cpp_stored()))
        # `TypeResolver.resolve_type` (Pending view/container resolution) --
        # the nested-def lambda header spells params/returns through it
        # (`resolve_type(t).to_cpp_param(name)`).
        # Identity for analyzer-only callers, whose value-scalar fixtures
        # never carry a Pending type.
        self.render_resolve = render_resolve or (lambda t: t)
        # The codegen concept renderer for `if constexpr` conditions
        # (protocol-isinstance): a (var_name, check_type, declared_type) ->
        # cpp callable built at the driver seam from the codegen concept
        # helpers. None for analyzer-only callers -- the constexpr
        # arm rejects without it.
        self.render_concept = render_concept
        # The receiver name (`self`) when `func` is an instance method, else
        # None: it lowers to a THIRSelf and, unlike a pointer binding, is not
        # a liftable borrow source (`_is_borrow_ptr_local` must never treat it
        # as one). `self_cpp` is its C++ spelling (`this` for a plain method,
        # `__self` for a resumable method coro) and `self_is_pointer` selects
        # `->` vs `.` field/method access -- a plain method's `this` is a
        # pointer (`->`), a coro's `__self` is a `Record&` reference (`.`).
        self.self_receiver = self_receiver
        self.self_cpp = self_cpp
        self.self_is_pointer = self_is_pointer
        # An enum companion's method: `self` is the companion's by-value
        # MEMBER, read as a plain name and copied into a closure (the
        # companion is a temporary wrapper).
        self.member_self = False
        # The owning record's name when `func` is a method: `_param_is_const`
        # resolves a record param's const verdict from the method's FunctionInfo
        # on this record, not the free-function registry.
        self.record_name = record_name
        # Type-param bounds in scope for this body -- the mirror of codegen's
        # `ctx.current_type_param_bounds` (record bounds, then the function's
        # own overriding them). Sema does not stamp bounds on the
        # TypeParamRef instances in expression types, so bounded-receiver
        # dispatch resolves them by name through this dict.
        self.tparam_bounds: dict = {}
        if record_name:
            ri = analyzer.registry.get_record(record_name)
            if ri is not None and ri.type_param_bounds:
                self.tparam_bounds.update(ri.type_param_bounds)
        if getattr(func, "type_param_bounds", None):
            self.tparam_bounds.update(func.type_param_bounds)
        # Per-@overload-stub lowering: the dead-branch-elim narrowing map
        # (build_overload_narrowing's stub-name -> concrete type) and the
        # stub's return type (overriding func.return_type at the return
        # arms). Both None for an ordinary single-signature body.
        # `overload_terminated` mirrors ctx.overload_terminated: a folded
        # True branch ending in return/raise truncates every enclosing
        # statement list and suppresses the trailing-comment scan.
        self.overload_narrowing: 'dict[str, TpyType] | None' = None
        self.overload_stub_return: 'TpyType | None' = None
        # A literal-only group's per-stub fact map (codegen's
        # literal_overload_facts): enables the if-chain dead-branch fold
        # exactly like overload_narrowing does for the isinstance families.
        self.overload_literal_facts: dict[str, TpyType] = {}
        self.overload_terminated: bool = False
        # The enclosing functions a nested def captures names FROM,
        # outermost first. A capture's borrow is decided where the name is
        # DEFINED, so a predicate keyed on the enclosing signature (the
        # param const verdicts) has to ask that function and not `func`,
        # which inside a lambda is the nested one.
        self.capture_funcs: tuple = ()
        # Walrus targets already pre-declared this FUNCTION -- codegen's
        # walrus_pre_declared asymmetry (function-scoped, never
        # branch-restored, unlike the branch-copied `declared` dict): a
        # sibling-branch re-bind assigns in place, no second decl.
        self.walrus_predeclared: set[str] = set()
        # Owned-slot walrus targets (`std::optional<T> n;` + walrus-deref
        # reads): every read renders `(*n)` with DOT member access --
        # codegen's register_walrus_deref substitution, NOT the pointer-local
        # arrow model. Function-scoped like the decl itself.
        self.walrus_slot_locals: set[str] = set()
        # First-declared @dynamic protocol locals that are reassigned: their
        # reseat statements take the rebind emit (hoisted optional slot).
        self.dyn_protocol_locals: set[str] = set()
        # Branch-hoisted `T*` pointer-locals WITHOUT an if-head rebind slot
        # (reassigned but not rvalue-reassigned): an rvalue reseat allocates
        # its slot lazily at function top (PtrSlotKind.BRANCH_RVALUE);
        # codegen's ctx.branch_hoisted_vars.
        self.branch_hoisted: set[str] = set()
        # Captures a routed match hoisted as `T*` pointer-locals. A NESTED
        # match may re-seat one (`q = &(__match_subject_2.inner);`); every
        # other pointer-local reuse keeps rejecting, so the admission keys
        # on this set rather than on the record's `pointer`.
        self.match_ptr_hoists: set[str] = set()
        # Deref-view narrowed subjects (the if-init cast local): branch-body
        # member calls carrying deref_narrowed_to read the wrapper var via
        # this spelling (`(*__b_ptr)`), never the deref chain. Registered
        # per branch by the deref-view narrow-if arm.
        self.deref_view_spelled: dict[str, str] = {}
        # Forwarded proto-param aliases (`xs = it`): the local's decl emits
        # nothing and every read renders the BACKING param's name
        # (codegen's generator_storage_name substitution). Resumable-only.
        self.forwarded_map: dict[str, str] = {}
        # Resumable CFG leaves reuse statement lowering for compound bodies.
        # Their nested frame writes and async-return shapes reject at the
        # statement arm rather than through a predictive leaf-tree scan.
        self.resumable_leaf_mode = False
        # Whether the expressions being lowered sit in a statement a hoisted
        # temporary can precede (`placement_scope`). A member-init, a
        # base-init argument and a lambda's single-expression body have no
        # such statement: a decl hoisted "before the statement" would land
        # in front of the constructor or outside the lambda.
        self.placement = SlotPlacement.STATEMENT
        # The statement being lowered declares pending named slots before
        # its first line (see `_SLOT_HOIST_STMTS`); off everywhere else, so
        # an unclassified position cannot place one after its use.
        self.slot_hoist_ok = False
        # True anywhere inside a container-element subtree, sticky through
        # nesting: the Optional ternary carve-out is only CORRECT at the
        # immediate element (a pointer-form arm deeper in renders ill-formed
        # C++), so this flag exists to REJECT those deeper positions.
        self.in_container_elem = False
        # Returns nested in leaf compounds (THIRResumableReturn), collected
        # here so `lower_resumable` can register their values into the body's
        # `return_values` table (the seam's id(ast)-keyed lookup).
        self.nested_returns: list = []
        # True while lowering a helper-based finally body: a `return` there
        # needs the helper's Poll-replay / __finally_stop renders, a named
        # rung -- reject instead of routing through the leaf-return hook
        # (generator bare returns route; see the dispatch return arm).
        self.in_finally_helper = False
        # Frame fields whose decl/reassign renders as the plain position-
        # blind `name = expr;` member assign (frame_fields minus the
        # frame_slot / borrow-tuple / coro-handle families, whose renders
        # differ). The branch-nested decl arm keys on it; populated only by
        # `lower_resumable`, empty for every sync body.
        self.plain_frame_fields: frozenset = frozenset()
        # Borrow-form tuple frame fields (`std::tuple<..., T*>` bare members)
        # and concrete-coro handle slots -- the two frame_slot-adjacent
        # families whose write render differs from both the plain assign and
        # the bare emplace. The branch-nested decl arm keys on them;
        # populated only by `lower_resumable`, empty for every sync body.
        self.borrow_tuple_frame_locals: frozenset = frozenset()
        self.coro_handle_slots: frozenset = frozenset()
        # Resolved frame-local types (the frame ctx's `var_types`): the
        # slot's declared C++ shape, which is NOT the first decl's expression
        # type -- a branch-declared container literal can resolve to a sized
        # Array at one decl site while the frame slot is the merged list.
        # Renders that spell the SLOT (the frame_slot brace-init prefix) must
        # read this, not `declared`. Populated only by `lower_resumable`.
        self.frame_local_types: dict = {}
        # Every name the resumable frame stores as a MEMBER -- the layout's
        # own field set (params + generator locals), unlike
        # `frame_local_types`, which holds locals only. A capture list has to
        # see the whole set: a member is not a variable, so a lambda reads it
        # into an init-capture instead of naming it. Populated only by
        # `lower_resumable`, empty for every sync body.
        self.frame_field_names: frozenset = frozenset()
        # Value/storage tuple frame fields (`std::tuple<...>` bare members):
        # the unpack arm ref-binds one as a name source
        # (`const auto& __tup_N = <name>;`). Populated only by
        # `lower_resumable`, empty for every sync body.
        self.value_tuple_frame_locals: frozenset = frozenset()
        self.opt_tuple_holders: frozenset = frozenset()
        self.opt_ptr_frame_locals: frozenset = frozenset()
        self.rebind_ptr_frame_locals: frozenset = frozenset()
        # One-shot `__await_lift_*` frame temps (the skeleton's
        # `one_shot_lift_names`): the unpack arm rvalue-ref-binds one as a
        # consumable source (`auto&& __tup_N = (*<name>);`) and moves its
        # owned elements out. Populated only by `lower_resumable`, empty
        # for every sync body.
        self.oneshot_lift_locals: frozenset = frozenset()
        # Pointer-alias frame locals (the skeleton's pointer_alias_locals,
        # minus the synthetic decomposition temps): `T*` fields aliasing
        # live storage. Reads ride a pointer record; the unpack arm binds one
        # via `= &(unwrap_ref(tuple_elem_ref(...)));` (frame_ptr_elem).
        # Populated only by `lower_resumable`, empty for every sync body.
        self.alias_ptr_locals: frozenset = frozenset()
        # The subset of `alias_ptr_locals` whose frame field is `const T*`
        # (the frame layout's own const verdict). A lift that spells the
        # element pointers must agree with the field, so the unpack arm
        # reads this rather than the SYNC receiver const-verdict, which the
        # frame does not follow. Populated only by `lower_resumable`.
        self.const_alias_ptr_locals: frozenset = frozenset()
        # Every name this frame binds to const storage (the frame's own
        # `const_frame_bindings`): the const-borrow captures and `@readonly`
        # receiver as well as the locals above. A lift off one of them spells
        # `const T*` element pointers, because that is what the capture is.
        # Populated only by `lower_resumable`.
        self.const_frame_bindings: frozenset = frozenset()
        # Pointer-form tuple-unpack LOOP targets (the skeleton's
        # pointer_form_unpack_targets): `T*` fields the head unpack
        # re-points via `= &(std::get<i>(__tup_N));` off the deref'd
        # pointer holder. Populated only by `lower_resumable`.
        self.unpack_ptr_targets: frozenset = frozenset()
        # Unpack holders that point into a step result whose C++ type only the
        # iteration source knows: a member may be a value, a reference or a
        # borrow pointer, so an alias target takes the normalizing read.
        self.source_typed_holders: frozenset = frozenset()
        # The residue ledger must read the SAME facts `prescan.hoisted` does:
        # the module-init walk's carrier is synthetic, so its `id(func)` has no
        # analyzer entry and only the override carries its hoisted set. Left
        # unseeded, this reject backstop silently admits a body it should
        # reject (top-level hoisting is reachable -- `module_init_local` temps
        # are locals of `__tpy_init`, not globals).
        self.unhandled_hoists = set(
            analyzer.function_hoisted_vars.get(func, ())
            if hoisted_override is None else hoisted_override)
        # Resumable-frame tuple locals whose per-element OWNERSHIP is not in
        # their declared type: a literal-bound owning/mixed frame slot, mapped
        # to the effective `Own[...]`-marked tuple (codegen's
        # `FrameLocalLayout.effective_type`). The element-read arrow chooser
        # and the slot's write both read the ownership off this, since a
        # literal's inferred type has no place to record it.
        self.frame_own_tuple_types: dict[str, 'TpyType'] = {}
        self._alias_taken_memo: 'tuple | None' = None
        # F2e: sema's RAW owned-locals fact, the mirror of codegen's
        # `ctx.sema_movable_locals`. It means "sema proved this local owned",
        # NOT "movable" -- a binding is movable only where its record says
        # so (the planner's promoting rows); a consumer moving off this
        # directly re-introduces the conflation that made a value-typed
        # local (a view-promoted `str`, a BigInt) and a ptr-variant alias
        # move where they must be copied. A reject may ask it: "sema owns
        # this name".
        self.sema_movable_locals: frozenset[str] = frozenset(
            analyzer.function_movable_locals.get(func, ()))
        # U3/U4 isinstance-narrowing scope (see _NarrowScope's docstring),
        # snapshot/restored around branch and loop bodies.
        self.narrow = _NarrowScope()
        # U4 compound conditions: subject -> (member_cpp, is_ptr) for the
        # alias-free THIRNarrowedRead inside the condition. Strictly
        # condition-scoped -- installed and popped by _lower_narrow_cond,
        # never live across statements (deliberately OUTSIDE _NarrowScope).
        self.inline_narrowed: dict[str, tuple[str, bool, THIRUnionExtraction | None]] = {}
        self.forbidden_reads: set[str] = set()
        self.forbidden_writes: set[str] = set()
        # Closure locals bound by a lowered TpyNestedDef. Sole consumer: the
        # Callable-return arm (`return add;`); closure CALLS key on
        # `fi.frame_captures` in `_free_callee_kind` instead. The name
        # survives the nested scope (Python names are function-scoped).
        self.nested_def_locals: set[str] = set()
        # The enclosing function's @error_return error type render
        # (ctx.current_error_return) -- set by lower_function; drives the
        # return pass-through / return-tier raise admissions and rides
        # THIRFunction into the emit state.
        self.error_return_cpp: 'str | None' = None
        # The binding table and the scope node the walk is at
        # (`bindings.plan_and_install`, before the walk).
        self.table = None
        self.cursor = None
        self.shadow_report = None
        # The names a sink may move inside a `moves_only` region; None
        # outside one, where the binding's record decides.
        self.move_region: frozenset[str] | None = None

    @contextmanager
    def branch_scope(self):
        """One scope pop for every branch-scoped lc name-set (plus `narrow`).

        Restore is by whole-set snapshot, so it is symmetric: in-scope adds
        (branch-local decl registrations) AND removals (the for-each shadow)
        both undo at the pop. Registration that must outlive the scope is the
        caller's job -- perform it in the enclosing frame, after (or outside)
        this context."""
        # `.copy()` covers both shapes here: plain name-sets and the
        # name-keyed dicts.
        saved = [getattr(self, name).copy() for name in _BRANCH_SCOPED_SETS]
        saved_narrow = self.narrow.snapshot()
        try:
            yield
        finally:
            for name, entries in zip(_BRANCH_SCOPED_SETS, saved):
                setattr(self, name, entries)
            self.narrow = saved_narrow

    def alias_taken(self) -> 'frozenset[str]':
        """Names a synthesized narrowing alias must not take -- the one
        registry every minting site consults. See `narrow_alias_taken` for
        the body-wide half; on top of it come the aliases already live in an
        enclosing scope (`_NarrowScope.live_aliases` and its statement-level
        subset `persistent_aliases`), which pop with their branch -- a
        resumable BB's re-established extractions register there too, for the
        BB they are live in.

        The body-wide half is memoized against the state it reads (the
        prescan object, which a nested-def scope SWAPS, the frame's field
        set and the receiver spelling) rather than recomputed per mint: it
        unions every module global, and a narrowing-heavy body mints many."""
        key = (self.prescan, self.frame_field_names,
               self.self_receiver is not None and self.self_cpp != "this")
        memo = self._alias_taken_memo
        if memo is None or memo[0] != key:
            memo = (key, narrow_alias_taken(
                self.prescan.bound_names, self.frame_field_names,
                frame_self=key[2],
                module_globals=self.prescan.module_global_names))
            self._alias_taken_memo = memo
        return (memo[1] | self.narrow.live_aliases
                | self.narrow.persistent_aliases)

    @contextmanager
    def live_alias(self, name: str):
        """Hold `name` in the live-alias registry for a region the narrowing
        scope does not bracket -- the poly if-init cast pointer, whose C++
        scope is the whole `if`, both arms included."""
        self.narrow.live_aliases.add(name)
        try:
            yield
        finally:
            self.narrow.live_aliases.discard(name)

    @contextmanager
    def lambda_params(self, params: 'list[tuple[str, TpyType]]'):
        """Read a lambda's params as params inside its body: the closure
        spells them like a def's (a `str` param is a `std::string_view`), so
        the param-implies-view verdicts apply, and they shadow any enclosing
        name of the same spelling. The shadowed enclosing params leave
        `params` outright, so a first-match and a last-match (`dict`) lookup
        agree. (`bound_names` already holds lambda params: the body scan
        counts them.)"""
        with self.shadow_scope({n for n, _t in params}):
            prescan = self.prescan
            self.params = tuple(params) + self.params
            for f, inner in zip(_ParamFacts._fields,
                                _param_facts(params, self.analyzer)):
                setattr(prescan, f, getattr(prescan, f) | inner)
            yield

    def movable_now(self, name: str) -> bool:
        """Whether a sink here may move `name` at its last use -- the one
        question every move site asks: the binding's record, or the region
        policy inside a `moves_only` region."""
        if self.move_region is not None:
            return name in self.move_region
        rec = self.lookup_binding(name)
        return rec is not None and rec.movable

    def binding(self, name: str) -> BindingRepr:
        """The record `name` resolves to where the walk is -- the one
        accessor every per-name representation fact is read through -- or
        `UNBOUND` for a name the table does not bind (a function, a class,
        a synthesized alias, the narrowed `Any` global)."""
        rec = self.lookup_binding(name)
        return UNBOUND if rec is None else rec

    def prior_binding(self, name: str) -> BindingRepr:
        """The record `name` resolves to BEFORE the statement being lowered
        declares anything: a declaration arm asking about an earlier
        binding of the name it is about to declare."""
        key, node = self.cursor
        rec = node.lookup_before(name, key)
        return UNBOUND if rec is None else rec

    def names_with(self, fact: str, *, prior: bool = False) -> NameFacts:
        """The names whose record has `fact`, resolved where the walk is
        NOW (or, with `prior`, before the statement's own declarations),
        for a helper that takes a name set -- a later move of the cursor
        does not change the answer."""
        assert fact in BindingRepr.__dataclass_fields__, fact
        key, node = self.cursor
        if fact == "movable" and self.move_region is not None:
            region = self.move_region
            return NameFacts(region.__contains__)

        def look(n: str) -> bool:
            rec = (node.lookup_before(n, key) if prior
                   else node.lookup(n))
            return rec is not None and bool(getattr(rec, fact))
        return NameFacts(look)

    def lookup_binding(self, name: str) -> 'BindingRepr | None':
        """The record `name` resolves to where the walk is: the scope chain
        at the cursor, and nothing else -- a miss is a name the table does
        not bind here."""
        return self.cursor[1].lookup(name)

    def declaring(self, name: str) -> BindingRepr:
        """The record the statement being lowered writes for `name`, for
        the arm that declares it: the binding it reassigns where the chain
        has one, else the record its own site declares, which enters the
        chain only after the statement. A site declaring no record, or two
        of that name, is an internal error."""
        key, node = self.cursor
        rec = node.lookup(name)
        if rec is not None:
            return rec
        rec = self.table.declared_by(key, name) if key is not None else None
        if rec is None:
            raise BindingTableError(
                f"no record for {name!r} declared at "
                f"{type(key).__name__} in {self.func.name}")
        return rec

    @property
    def handed_over_params(self) -> frozenset[str]:
        """The params the caller hands over, which a constructor member-init
        may move: the `Own` params (their records' `owned`) and the
        ownership-transfer tuples (`param_takes_ownership` --
        `std::tuple<A, B*>&&`), which an `owned` record leaves out."""
        root = self.table.root.bindings
        return frozenset(
            pname for pname, ptype in self.params
            if (pname in root and root[pname].owned)
            or (isinstance(ptype, TpyType)
                and param_takes_ownership(ptype)))

    def any_frame_slot(self) -> bool:
        """Whether some frame field visible here is a `frame_slot<T>`: a
        body nested in a resumable one (a frame member def) keeps the
        frame's protocol-iterable fence while it can see one."""
        return any(self.binding(n).frame_slot for n in self.frame_field_names)

    @property
    def may_hoist(self) -> bool:
        """Whether a statement encloses the expression being lowered, so a
        declaration may be hoisted before it -- whatever the immediate use
        grants. Read where a hoisting node is created (`_hoisted`), where a
        select would emplace a slot, and where an optional iterator temp
        is a render choice."""
        return self.placement is SlotPlacement.STATEMENT

    @contextmanager
    def placement_scope(self, placement: SlotPlacement):
        """Lower a region under `placement` (see `SlotPlacement`). A region
        with its own flush point inside a no-flush one (a comprehension's
        loop body) reopens STATEMENT."""
        saved = self.placement
        self.placement = placement
        try:
            yield
        finally:
            self.placement = saved

    @contextmanager
    def moves_only(self, names: AbstractSet[str]):
        """Lower a region whose sinks may move only `names`: a constructor
        member-init (its `handed_over_params` -- the list runs before the
        body, so no local exists yet) or a comprehension element (its loop
        variable -- an outer name would be moved once per iteration)."""
        saved = self.move_region
        self.move_region = frozenset(names)
        try:
            yield
        finally:
            self.move_region = saved

    @contextmanager
    def shadow_scope(self, names: AbstractSet[str],
                     declared: 'dict[str, TpyType] | None' = None):
        """For an inner scope (nested def, lambda) binding `names`, strip them
        from every inherited per-name fact until exit; yields `declared`
        minus `names` as a new dict (None when not passed)."""
        names = frozenset(names)
        prescan = self.prescan
        saved_lc = [getattr(self, a) for a in _SHADOWED_LC_STATE]
        saved_prescan = [getattr(prescan, a) for a in _SHADOWED_PRESCAN_FACTS]
        saved_params = self.params
        saved_narrow = self.narrow
        saved_receiver = self.self_receiver
        saved_member_self = self.member_self
        for a, v in zip(_SHADOWED_LC_STATE, saved_lc):
            setattr(self, a, _without_names(v, names))
        # Prescan facts are rebound, never mutated in place, once the walk
        # starts, so an untouched one can be shared with the inner scope.
        for a, v in zip(_SHADOWED_PRESCAN_FACTS, saved_prescan):
            setattr(prescan, a, _without_names(v, names, shared=True))
        self.params = tuple(p for p in saved_params if p[0] not in names)
        narrow = saved_narrow.snapshot()
        for f in _SHADOWED_NARROW_FACTS:
            setattr(narrow, f, _without_names(getattr(narrow, f), names,
                                              shared=True))
        self.narrow = narrow
        # An inner param spelled `self` is not the method's receiver, nor
        # an enum companion method's member.
        if saved_receiver in names:
            self.self_receiver = None
        if "self" in names:
            self.member_self = False
        try:
            yield (None if declared is None
                   else _without_names(declared, names))
        finally:
            for a, v in zip(_SHADOWED_LC_STATE, saved_lc):
                setattr(self, a, v)
            for a, v in zip(_SHADOWED_PRESCAN_FACTS, saved_prescan):
                setattr(prescan, a, v)
            self.params = saved_params
            self.narrow = saved_narrow
            self.self_receiver = saved_receiver
            self.member_self = saved_member_self


def _walrus_pairs(expr):
    """(target, value) for every walrus node in `expr` -- a generic
    dataclass-field recursion over the Tpy nodes."""
    if expr is None or not isinstance(expr, TpyExpr):
        return
    if isinstance(expr, TpyNamedExpr):
        yield expr.target, expr.value
    for f in fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            yield from _walrus_pairs(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    yield from _walrus_pairs(item)


def binding_source_sites(body) -> 'Iterator[tuple[str, TpyExpr, object, bool]]':
    """Every binding source in a body, in source order: `(target, source,
    statement, is_walrus)` for each initialized VarDecl, each assignment to
    a bare name and each walrus inside a statement's own expressions."""
    for stmt in body:
        if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
            yield stmt.name, stmt.init, stmt, False
        elif isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
            yield stmt.target.name, stmt.value, stmt, False
        for e in stmt.exprs():
            for tgt, src in _walrus_pairs(e):
                yield tgt, src, stmt, True
        for sub in stmt.sub_bodies():
            yield from binding_source_sites(sub)


def _btuple_const_root(name: str, lc: _LowerCtx) -> bool:
    # `const_ref_params` | `const_indirect_locals` mirror: the inferred
    # const-borrow param verdict plus the const-classified locals (which
    # carry the readonly-method receiver).
    return (lc.binding(name).const
            or _param_is_const(name, lc.func, lc.analyzer, lc.record_name))


def _btuple_const_storage(expr, lc: _LowerCtx) -> bool:
    # `is_const_storage_source` mirror (including the
    # `is_const_union_source` lvalue-chain half).
    def const_root(e, stepped: bool) -> bool:
        if not isinstance(e, TpyName):
            return False
        if _btuple_const_root(e.name, lc):
            return True
        b = lc.binding(e.name)
        return not stepped and (b.const_storage_tuple or b.const_borrow_tuple
                                or b.const_opt_borrow_tuple)
    return const_place(expr, lc.analyzer.get_expr_type, const_root)


@dataclass
class _LowerScope:
    """The complete live statement-lowering view.

    Function-wide representation state remains owned by `_LowerCtx`; lexical
    bindings and control position vary per nested body. Keeping both behind
    one carrier keeps lowering as the authoritative sequential walk.
    """
    lc: _LowerCtx
    declared: dict[str, TpyType]
    in_branch: bool = False
    branch_decls_ok: bool = False
    loop_depth: int = 0

    def admission_pointers(self) -> NameFacts:
        """Pointer locals whose statement admission follows pointer rules:
        every pointer binding except the Optional-ptr borrow of a RECORD,
        whose bare-name reads stay bare and reach members through `->` (its
        admissions are the Optional record route's). A container pointee
        derefs at value positions like any other pointer binding
        (`__setitem__((*xs), i, v)`), so it stays under the pointer rules.
        Asked at the statement's entry, before its own declarations."""
        pointers = self.lc.names_with("pointer", prior=True)
        declared = dict(self.declared)
        analyzer = self.lc.analyzer

        def admitted(name: str) -> bool:
            if name not in pointers:
                return False
            opt = _optional_ptr_borrow(declared.get(name), analyzer)
            return opt is None or not _record_class_binding(opt.inner)
        return NameFacts(admitted)

