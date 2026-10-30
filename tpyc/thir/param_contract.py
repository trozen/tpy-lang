"""The parameter contract: what a resolved callee's signature promises about
one parameter, derived once and read by THIR argument lowering and by MIR.

Each fact is asked of the callee and the parameter index (an argument maps
to its parameter through `ArgBinding.at`; a call with no binding, and an
operator operand, keys on the resolved signature and the index), never of
the argument or the call's kind:

- passing: the `ParamPassing` the signature renders. The published one
  (`THIRCallableSignature.passings`, `THIRParam.passing`) is read; a stub
  family publishes the one `signature_passings` derives from its declared
  const verdicts.
- const: the callee's const verdict (`FunctionInfo.const_borrow_params`,
  the materialized `decide_param_const`), or a declared `readonly[...]`.
- pointer: a by-value `Ptr[T]` apart from a borrow pointer (the Optional /
  union borrow form), and whether the pointer's pointee is const.
- materializes: who copies an lvalue into an owned parameter -- the
  caller (a `T&&` parameter) or the callee (a native type's insert stub,
  whose runtime overload takes `const T&`).
- retention: how the callee may keep the argument past the call -- sema's
  retention verdict (`retention_channel`), asked of the call the argument
  is passed through; None when it keeps nothing.
- frame_captured: the callee is a generator / coroutine factory whose
  frame holds the argument.
- adapts: a protocol parameter, deduced structurally or adapted to a
  `@dynamic` protocol.
- instantiation_storage: an open parameter of a callee with no TPy body (a
  runtime template, an inline `@cpp_template`): the C++ takes the
  instantiation's own storage, not a borrow pointer at it.
- declared_only: the callee's signature is declared rather than inferred
  from an analyzed body, so a reference parameter binds what it is handed.
- written: the callee writes through the parameter.
"""

from dataclasses import dataclass
from enum import Enum, auto

from ..codegen_cpp.param_const import decide_param_const, declared_param_consts
from ..parse.nodes import TpyExpr
from ..sema.call_lends import Retention, retention_channel
from ..type_def_registry import ParamPassing, has_view_param_form
from ..typesys import (
    CONST_PARAMS_METHODS, FunctionInfo, NominalType, OwnType, PtrType, ReadonlyType, ResolvedBinop, ResolvedUnaryop,
    TpyType, TypeParamRef, contains_type_param, is_bodyless_binding, is_dyn_protocol,
    is_protocol_type, is_readonly_ptr, param_takes_ownership, pointer_repr_optional, pointer_variant_union,
    unwrap_own, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..value_category import ExprTypeOf, frame_factory_callee
from .nodes import THIRCallableSignature, THIRParam


class PointerKind(Enum):
    """How a POINTER-passed parameter points: a `Ptr[T]` passed by value,
    or the borrow form of an Optional / union (`T*`, `Union<A*, B*>`)."""
    NONE = auto()
    BY_VALUE = auto()
    BORROW = auto()


class Materializer(Enum):
    """Who copies an lvalue argument into an owned parameter."""
    # A `T&&` parameter binds no lvalue: the caller copies into a temporary
    # and moves it in.
    CALLER = auto()
    # A native type's insert stub (`push_back`, `insert`, `__setitem__`):
    # its runtime overload takes `const T&` and copies itself.
    CALLEE = auto()


class Adapts(Enum):
    """How a protocol parameter takes its argument."""
    STRUCTURAL = auto()
    DYNAMIC = auto()


@dataclass(frozen=True)
class ParamContract:
    """The facts above for one parameter of one resolved callee."""
    passing: ParamPassing | None
    const: bool
    pointer: PointerKind
    const_pointee: bool
    materializes: Materializer | None
    retention: Retention | None
    frame_captured: bool
    adapts: Adapts | None
    instantiation_storage: bool = False
    declared_only: bool = False
    written: bool = False


# --- passing


def signature_passings(types: tuple[TpyType, ...], consts: tuple[bool, ...]) -> tuple[ParamPassing, ...]:
    """How each parameter is passed at its const verdict -- the fact a
    definition's `THIRParam.passing` publishes, asked of the same types."""
    return tuple(t.param_passing(const) for t, const in zip(types, consts, strict=True))


def stub_param_consts(fi: FunctionInfo) -> tuple[bool, ...]:
    """The const verdict a body-less stub declares per parameter: a
    `readonly[...]` parameter, or a whole-callable promise -- `@pure`, or
    `@readonly` on a free function, which sema reads as "mutates no
    argument" for a callee it cannot analyze (on a method it is a receiver
    fact only)."""
    return declared_param_consts(tuple(p.type for p in fi.params), is_pure=fi.is_pure,
                                 is_readonly=fi.is_readonly, is_method=fi.is_method)


def method_stub_param_consts(fi: FunctionInfo, types: tuple[TpyType, ...]) -> tuple[bool, ...]:
    """A method stub's const verdicts over `types`, its receiver first: the
    method's `@readonly` for the receiver, then each declared parameter's,
    a parameter passed as a view (a `str` key) included -- nothing writes
    through one."""
    return (fi.is_readonly, *(const or t.param_passing(False) is ParamPassing.VIEW
                              for const, t in zip(stub_param_consts(fi), types[1:], strict=True)))


def published_passing(signature: THIRCallableSignature | None, index: int) -> ParamPassing | None:
    """The passing `signature` publishes for parameter `index`; None when it
    publishes none (a hand-built signature) or has no such parameter."""
    if signature is None or signature.passings is None or not 0 <= index < len(signature.passings):
        return None
    return signature.passings[index]


def bound_passing(p: THIRParam) -> ParamPassing | None:
    """The passing a definition publishes for its parameter `p`; None when
    unpublished (a hand-built body)."""
    return p.passing if isinstance(p.passing, ParamPassing) else None


# --- const


def param_const(fi: FunctionInfo | None, index: int) -> bool:
    """Whether the callee's const verdict makes parameter `index` a const
    borrow (`const_borrow_params`, kept on the raw FunctionInfo: a
    substituted one reads it through its root). False for no callee."""
    if fi is None:
        return False
    verdict = fi.const_borrow_params
    return bool(verdict) and index in verdict


def forced_param_const(fi: FunctionInfo | None, index: int, ptype: TpyType | None) -> bool | None:
    """The const verdict of a callee whose signature renders the
    parameters it does not write const -- an in-place operator's every
    parameter, a constructor's open `T` (which the inferred verdict leaves
    to the instantiation): `decide_param_const`'s forced verdict, which
    drops const for a written or address-escaping parameter. None where
    nothing is forced: the verdict is inferred (`param_const`), or a
    binding's declaration states it (`stub_param_consts`)."""
    if fi is None or not isinstance(ptype, TpyType):
        return None
    inplace = fi.is_method and fi.name in CONST_PARAMS_METHODS
    if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype))), TypeParamRef):
        forced = inplace or fi.is_constructor or (fi.is_method and fi.name == "__init__")
    else:
        forced = inplace and not is_bodyless_binding(fi)
    if not forced:
        return None
    return decide_param_const(ptype, index=index, pname="", mutated_params=fi.mutated_params,
                              addr_escapes_params=fi.addr_escapes_params or frozenset(),
                              const_params=True).signature_const


def const_ref_slot(ptype: TpyType | None, const_verdict: bool) -> bool:
    """The parameter renders a CONST reference: declared readonly, or the
    callee's const verdict (`param_const`)."""
    return const_verdict or isinstance(ptype, ReadonlyType)


# --- pointer


def _bare(ptype: TpyType) -> TpyType:
    return unwrap_readonly(unwrap_send_sync(unwrap_ref_type(ptype)))


def pointer_kind(ptype: TpyType | None) -> PointerKind:
    """A `Ptr[T]` parameter passes the pointer by value; an Optional or a
    union of reference types passes its borrow form, a pointer at the
    caller's storage (`pointer_repr_optional`, `pointer_variant_union`)."""
    if not isinstance(ptype, TpyType):
        return PointerKind.NONE
    if isinstance(_bare(ptype), PtrType):
        return PointerKind.BY_VALUE
    if pointer_repr_optional(ptype) is not None or pointer_variant_union(ptype) is not None:
        return PointerKind.BORROW
    return PointerKind.NONE


def slot_deep_const(ptype: TpyType, const_verdict: bool) -> bool:
    """A borrow pointer slot spells const pointees (`const A*`,
    `Union<const A*, const B*>`): declared readonly, or the callee's const
    verdict."""
    return isinstance(unwrap_send_sync(ptype), ReadonlyType) or const_verdict


def const_pointee(ptype: TpyType | None, const_verdict: bool) -> bool:
    """Whether a pointer parameter's pointee is const: a `Ptr[readonly[T]]`
    (`const T*`), or a deep-const borrow pointer (`slot_deep_const`)."""
    match pointer_kind(ptype):
        case PointerKind.BY_VALUE:
            return is_readonly_ptr(_bare(ptype))
        case PointerKind.BORROW:
            return slot_deep_const(ptype, const_verdict)
    return False


# --- materializes


def native_insert_stub(fi: FunctionInfo | None) -> bool:
    """A body-less method of a native type (list, dict, set, ...): its
    `Own[T]` parameter is an insert whose runtime overload copies an
    lvalue itself."""
    return fi is not None and fi.is_method and fi.name != "__init__" and is_bodyless_binding(fi)


def materializes(fi: FunctionInfo | None, ptype: TpyType | None) -> Materializer | None:
    """Who copies an lvalue into parameter `ptype` when it takes ownership
    (`param_takes_ownership`); None when it does not. A body-less binding's
    expansion binds an lvalue of a plain value type itself (a `str` / `bytes`
    buffer it may move from is the caller's copy)."""
    if not isinstance(ptype, TpyType) or not param_takes_ownership(ptype):
        return None
    if native_insert_stub(fi):
        return Materializer.CALLEE
    payload = _bare(unwrap_own(_bare(ptype)))
    if (fi is not None and is_bodyless_binding(fi) and payload.is_value_type()
            and not has_view_param_form(payload)):
        return Materializer.CALLEE
    return Materializer.CALLER


# --- retention


def retention(fi: FunctionInfo | None, index: int,
              call: TpyExpr | None = None,
              type_of: ExprTypeOf | None = None,
              arg: TpyExpr | None = None,
              recv: TpyExpr | None = None) -> Retention | None:
    """How callee `fi`, reached through `call` (or receiver `recv` where
    no call node states it), keeps argument `index` past the call -- sema's
    one retention verdict (`retention_channel`) -- None when it keeps
    nothing, so the argument may hold a borrow of storage this statement
    kills. An argument with no parameter index fails closed."""
    if index < 0:
        return Retention.MEASURED
    return retention_channel(fi, index, call, type_of, arg, recv)


def arg_retention(fi: FunctionInfo | None, index: int,
                  call: TpyExpr | None, analyzer,
                  arg: TpyExpr | None = None,
                  recv: TpyExpr | None = None) -> Retention | None:
    """`retention` typed by `analyzer`: the verdict a call site takes for
    its argument `index`."""
    return retention(fi, index, call, analyzer.get_expr_type, arg, recv)


def operand_retention(resolved: ResolvedBinop | ResolvedUnaryop | None,
                      operand: TpyExpr, recv: TpyExpr,
                      analyzer) -> Retention | None:
    """How the dunder an operator resolved to keeps one of its operands past
    the operation (`retention_channel`), None when it keeps nothing. The
    operand IS an argument of that dunder: its receiver (-1) when it is
    `recv` -- the left operand, the right one of a reflected dunder, a
    unary operator's only one -- else its parameter 0. An operator with no
    dunder (a C++ operator over primitives) only reads its operands."""
    if resolved is None or resolved.method is None:
        return None
    return retention_channel(resolved.method, -1 if operand is recv else 0,
                             None, analyzer.get_expr_type, operand, recv)


# --- frame_captured


def frame_captured(fi: FunctionInfo | None) -> bool:
    """The callee is a generator or coroutine factory: its frame borrows its
    reference arguments past the statement (`frame_factory_callee`, which
    also sees an `@overload`-ed generator)."""
    return fi is not None and (frame_factory_callee(fi) or fi.is_async)


# --- adapts


def protocol_slot(ptype: TpyType | None) -> NominalType | None:
    """The protocol a parameter names: a bare (or `readonly[P]` / `Send[P]`)
    protocol, `@dynamic` or structural; None for anything else.

    `Own[P]` (`::tpy::make_adapter<Base>` / `std::make_unique`) and the
    `Optional[P]` / protocol-union parameters are not protocol parameters
    here: their renders (typed null, `&(...)` address-of, `optional_to_ptr`)
    are the borrow pointer's.

    An `Own[...]` TYPE ARG (`Iterable[Own[T]]`, a `T_p&&` forwarding-ref
    slot) is a structural slot like any other: the call-argument render --
    not a protocol pre-arm -- rewrites a movable last-use arg into the consuming
    `::tpy::own_iter(std::move(x))`, and `_lower_call_arg`'s Iterable arm
    carries that wrap position-blind; every other arg takes the same
    bare/temp verdicts as a plain structural slot.
    """
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(ptype))
    # A `Send[P]` slot arrives as `Ref[Send[P]]` -- the marker is not
    # canonically outermost -- so peel the Ref and the marker again (Send
    # is erased wherever it sits).
    u = unwrap_readonly(unwrap_send_sync(unwrap_ref_type(u)))
    if isinstance(u, OwnType) or not is_protocol_type(u):
        return None
    return u


def adapts(ptype: TpyType | None) -> Adapts | None:
    """How a protocol parameter (`protocol_slot`) takes its argument."""
    proto = protocol_slot(ptype)
    if proto is None:
        return None
    return Adapts.DYNAMIC if is_dyn_protocol(proto) else Adapts.STRUCTURAL


# --- what the callee's declaration states


def instantiation_storage(fi: FunctionInfo | None, index: int) -> bool:
    """Parameter `index` of a callee with no TPy body (a binding, an
    inline `@cpp_template`) is declared over an open type or a protocol,
    either one a C++ template parameter: the template takes the
    instantiation's storage. An inline template specialized for the call
    (a builtin stub with no declaration of its own, its protocol
    parameters already substituted) declares none it could convert to."""
    if fi is None or not (is_bodyless_binding(fi) or fi.cpp_template is not None):
        return False
    root = fi.root
    if fi.cpp_template is not None:
        if root.declaration is None:
            return True
        params = root.declaration.params
        if not 0 <= index < len(params):
            return False
        declared = params[index][1]
        return contains_type_param(declared) or adapts(declared) is not None
    return 0 <= index < len(root.params) and contains_type_param(root.params[index].type)


def declared_only(fi: FunctionInfo | None) -> bool:
    """The callee's signature is its declaration's: a binding, or a body
    sema did not analyze (no mutation verdicts)."""
    return fi is not None and (is_bodyless_binding(fi) or fi.mutated_params is None)


def written(fi: FunctionInfo | None, index: int) -> bool:
    """The callee writes through parameter `index` (sema's mutation
    verdict)."""
    return fi is not None and index in (fi.root.mutated_params or ())


# --- the contract


def param_contract(fi: FunctionInfo | None, index: int, ptype: TpyType | None, *,
                   signature: THIRCallableSignature | None = None,
                   published: ParamPassing | None = None,
                   call: TpyExpr | None = None,
                   type_of: ExprTypeOf | None = None,
                   arg: TpyExpr | None = None,
                   recv: TpyExpr | None = None) -> ParamContract:
    """Every fact for parameter `index` (of declared type `ptype`) of the
    resolved callee `fi`, whose published signature is `signature` when it
    has one, or whose body publishes `published` for it. Where nothing is
    published (a stub or template family, a
    generic or resumable body, a base constructor), the passing is the one
    `ptype` takes at the const verdict -- a stub's declared one
    (`stub_param_consts`) included. A published `const T&` is const
    whatever the verdict says: the signature is what the callee renders.
    The retention is asked of the call context: `call` (or the receiver
    `recv` where no call node states it), argument `arg`, typed by
    `type_of` (`retention`)."""
    verdict = param_const(fi, index) or bool(forced_param_const(fi, index, ptype)) or bool(
        fi is not None and is_bodyless_binding(fi) and 0 <= index < len(fi.params)
        and stub_param_consts(fi)[index])
    passing = published if published is not None else published_passing(signature, index)
    if passing is None and isinstance(ptype, TpyType):
        passing = ptype.param_passing(const_ref_slot(ptype, verdict))
    return ParamContract(
        passing=passing,
        const=const_ref_slot(ptype, verdict) or passing is ParamPassing.CONST_REF,
        pointer=pointer_kind(ptype),
        const_pointee=const_pointee(ptype, verdict),
        materializes=materializes(fi, ptype),
        retention=retention(fi, index, call, type_of, arg, recv),
        frame_captured=frame_captured(fi),
        adapts=adapts(ptype),
        instantiation_storage=instantiation_storage(fi, index),
        declared_only=declared_only(fi),
        written=written(fi, index))


def pack_element_contract(pack_retention: Retention | None) -> ParamContract:
    """A `*args` pack's value element: storage of the pack's array, held by
    value whatever its type's parameter passing (a `str` element owns its
    buffer); the pack, not the element, is what the callee receives, so the
    element is kept as the pack is (`pack_retention`)."""
    return ParamContract(
        passing=ParamPassing.VALUE, const=False, pointer=PointerKind.NONE,
        const_pointee=False, materializes=None, retention=pack_retention,
        frame_captured=False, adapts=None)
