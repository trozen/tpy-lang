"""
The members a record's C++ declares, as sema and codegen both read them.

Signature-only facts (where a body can be defined, which receiver form a
member takes) decided once: codegen's placement plan and emitter, the
import-cycle gate (before body analysis) and sema's @dynamic slot check
walk the same list.
"""

from __future__ import annotations
from collections import namedtuple
from dataclasses import dataclass
from enum import Enum, auto

from .typesys import (
    TpyType, NominalType, OwnType, LiteralType, NONE,
    is_protocol_type, is_dyn_protocol, is_fn_type,
    unwrap_readonly, unwrap_ref_type, protocol_param_alternatives,
)
from .parse import TpyFunction, TpyRecord
from .parse.nodes import DEL_HOOK, INIT_METHOD, MOVE_HOOK
from .type_def_registry import protocol_info_of
from .identity_map import IdentityMap

# Unified protocol parameter info: name, list of protocol types (1+), nullable flag
ProtocolParamInfo = namedtuple('ProtocolParamInfo', ['name', 'protocols', 'has_none'])


def resolve_protocol_type(typ: TpyType) -> TpyType:
    """Resolve a type, setting is_protocol flag if it's actually a protocol.

    During parsing, some types may be classified as records when they're
    actually protocols (e.g., imported protocols). This fixes that for codegen.
    """
    if isinstance(typ, NominalType) and not typ.is_protocol:
        # Probe with the protocol flag set so `protocol_info_of`'s
        # qname-derived TypeDef lookup matches the authoritative
        # `{module}.{name}` key.
        probe = typ.with_protocol_flag(is_protocol=True)
        if protocol_info_of(probe) is not None:
            return probe
    return typ


def static_protocol_params(
        params: list[tuple[str, TpyType]]) -> list[ProtocolParamInfo]:
    """Every static (non-@dynamic) protocol parameter of a signature:
    single protocols (bare or Optional) and protocol unions, each as
    ProtocolParamInfo(name, protocols, has_none). Reads signatures only, so
    the import-cycle gate can ask it before any body is analyzed."""
    result: list[ProtocolParamInfo] = []
    for pname, ptype in params:
        alts = protocol_param_alternatives(ptype, resolve_protocol_type)
        if alts is not None:
            result.append(ProtocolParamInfo(
                pname, [a for a in alts if a is not NONE], NONE in alts))
    return result


def signature_is_template(func: TpyFunction) -> bool:
    """Does `func`'s own signature render a C++ template head: declared type
    params, a static-protocol param (`T_<p>`) or an `Fn` param (`__F<i>`)?
    Signature-only, so codegen's placement and the import-cycle gate (which
    runs before body analysis) ask the same question."""
    if func.type_params:
        return True
    if static_protocol_params(func.params):
        return True
    return any(is_fn_type(pt) for _, pt in func.params)


def group_is_template(func: TpyFunction, stubs: list[TpyFunction]) -> bool:
    """An @overload group renders a template when ANY stub's signature does,
    even if the implementation's does not: a template stub must stay
    instantiable in the header, so the whole group is header-only."""
    return (signature_is_template(func)
            or any(signature_is_template(s) for s in stubs))


def protocol_return(return_type: TpyType) -> NominalType | None:
    """The protocol a return type renders through, or None. Unwraps
    Own[Protocol] so a consuming __iter__ returning Own[Iterator[T]] gets
    `auto` in C++; an abstract @dynamic P stays wrapped -- Own[P] lowers to
    std::unique_ptr<P> via OwnType.to_cpp(), and the protocol path would
    emit `P&` to a temporary."""
    unwrapped = unwrap_readonly(unwrap_ref_type(return_type))
    if (isinstance(unwrapped, OwnType) and is_protocol_type(unwrapped.wrapped)
            and not is_dyn_protocol(unwrapped.wrapped)):
        unwrapped = unwrapped.wrapped
    if is_protocol_type(unwrapped) and isinstance(unwrapped, NominalType):
        return unwrapped
    return None


def return_is_deduced(return_type: TpyType) -> bool:
    """The return renders `auto`: a non-@dynamic protocol return
    (`-> Iterator[T]`), whose C++ type is the return expression's."""
    proto = protocol_return(return_type)
    if proto is None:
        return False
    pi = protocol_info_of(proto)
    return not (pi and pi.is_dynamic)


def overload_stubs_are_literal_only(
    stubs: 'list', impl: TpyFunction,
) -> bool:
    """Check if overload stubs differ from the impl only by Literal annotations.

    When all stubs have the same C++ parameter types as the implementation
    (because they only differ by LiteralType vs base type), per-stub
    specialization would produce duplicate C++ definitions. In that case,
    emit just the implementation function. Shared with THIR's per-stub gate
    (a literal-only group emits through the mangled-name path, which never
    sets the per-stub interception key).
    """
    for stub in stubs:
        for (_, impl_ptype), (_, stub_ptype) in zip(impl.params, stub.params):
            if isinstance(stub_ptype, LiteralType):
                continue
            if stub_ptype != impl_ptype:
                return False
    return any(
        isinstance(ptype, LiteralType)
        for stub in stubs
        for _, ptype in stub.params
    )


@dataclass(frozen=True)
class MemberSignature:
    """What a member's signature says about where its body can be defined.
    ``template``: it has a C++ template head (its class's, its own, or its
    @overload group's), so the body must be visible wherever it is
    instantiated -- the header. ``deduced``: its return renders `auto`, which
    C++ needs before the member's first use. Signature-only: codegen's
    placement (`member_def`) and the import-cycle gate (which runs before
    body analysis) both read it."""
    template: bool
    deduced: bool


def member_signature(func: TpyFunction, *, class_generic: bool,
                     group_template: bool = False) -> MemberSignature:
    """`MemberSignature` of a member of a class that is generic or not, or of
    one @overload variant (`func` being its stub); ``group_template`` is the
    variant's group's `group_is_template`, which every variant shares."""
    return MemberSignature(
        template=(class_generic or group_template
                  or signature_is_template(func)),
        deduced=return_is_deduced(func.return_type))


_CTOR_DTOR_NAMES = (INIT_METHOD, DEL_HOOK, MOVE_HOOK)


class MemberKind(Enum):
    # A body emitted as itself (a `@dispatch` variant too).
    METHOD = auto()
    # One `@overload` stub's specialization of its group's implementation.
    VARIANT = auto()
    CTOR = auto()
    # `__del__` and its `__move__`: one partition.
    DESTRUCTOR = auto()
    # A generator / async method: declared in the struct, defined with its
    # frame.
    RESUMABLE = auto()
    # `@cpp_template`: no declaration, expanded at each call site -- a
    # use-site requirement rather than an emitted signature.
    EXPANDED = auto()


class RefQualifier(Enum):
    """The ref-qualifier of a member's receiver."""
    NONE = ""
    # The borrowing half of a `self: auto_own[Self]` pair.
    LVALUE = "&"
    # A consuming member (`self: Own[Self]`, `auto_own`'s consuming half).
    RVALUE = "&&"


@dataclass(frozen=True)
class EmittedMember:
    """One member of a record's C++: ``func`` owns the signature it declares
    (a variant's stub), ``body`` the body defined for it (the variant's
    implementation). ``form_source`` is the declaration whose receiver
    const-ness it takes: a variant of a ``literal_only`` group
    (`overload_stubs_are_literal_only`) is specialized from its
    implementation's signature, any other from its stub's, whose
    decorators state that overload's contract. ``ref`` is the receiver's
    ref-qualifier as emitted."""
    func: TpyFunction
    body: TpyFunction
    kind: MemberKind
    sig: MemberSignature
    ref: RefQualifier
    form_source: TpyFunction
    literal_only: bool = False

    @property
    def variant(self) -> bool:
        return self.kind is MemberKind.VARIANT

    @property
    def readonly(self) -> bool:
        """The receiver is const. Read when asked, since Phase 2's const
        inference settles `is_readonly` after the import-cycle gate has
        built the members."""
        return self.form_source.is_readonly and not self.func.is_staticmethod


def _ref_qualifier(kind: MemberKind, body: TpyFunction) -> RefQualifier:
    # A resumable member's factory takes no ref-qualifier, and a variant's
    # specialization copies only `is_consuming` from its body.
    if kind is MemberKind.RESUMABLE:
        return RefQualifier.NONE
    if body.is_auto_own_borrowing_clone and kind is not MemberKind.VARIANT:
        return RefQualifier.LVALUE
    if body.is_consuming:
        return RefQualifier.RVALUE
    return RefQualifier.NONE


def _member(func: TpyFunction, body: TpyFunction, kind: MemberKind,
            sig: MemberSignature, form_source: TpyFunction, *,
            literal_only: bool = False) -> EmittedMember:
    return EmittedMember(func, body, kind, sig, _ref_qualifier(kind, body),
                         form_source, literal_only)


def record_emitted_members(record: TpyRecord, overload_groups: IdentityMap,
                           overload_twins: IdentityMap) -> list[EmittedMember]:
    """Every member of a (non-native) record's C++ whose signature repeats a
    def's, with its signature facts. Signature-only: `member_def`'s
    ``small`` is the caller's to apply. An `@overload` implementation is
    emitted as its stubs' specializations (``overload_groups``), its clone
    twin (``overload_twins``) not at all."""
    generic = bool(record.type_params)
    out: list[EmittedMember] = []
    for method in record.methods:
        # The ctor and destructor family are entered once, below.
        if (method.name in _CTOR_DTOR_NAMES
                or (method.is_overload_stub and method.is_stub)):
            continue
        stubs = overload_groups.get(method)
        if method.is_generator or method.is_async:
            kind = MemberKind.RESUMABLE
        elif method.cpp_template is not None:
            kind = MemberKind.EXPANDED
        elif stubs:
            template = group_is_template(method, stubs)
            literal_only = overload_stubs_are_literal_only(stubs, method)
            out.extend(
                _member(stub, method, MemberKind.VARIANT,
                        member_signature(stub, class_generic=generic,
                                         group_template=template),
                        method if literal_only else stub,
                        literal_only=literal_only)
                for stub in stubs)
            continue
        elif method in overload_twins:
            continue
        else:
            kind = MemberKind.METHOD
        out.append(_member(method, method, kind,
                           member_signature(method, class_generic=generic),
                           method))
    init, del_, move = record.init_method, record.del_method, record.move_method
    if init is not None:
        out.append(_member(init, init, MemberKind.CTOR,
                           member_signature(init, class_generic=generic), init))
    if del_ is not None:
        class_sig = MemberSignature(template=generic, deduced=False)
        out.extend(_member(h, h, MemberKind.DESTRUCTOR, class_sig, h)
                   for h in (del_, move) if h is not None)
    return out
