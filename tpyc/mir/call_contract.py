"""Immutable certificates for the bounded call interface."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..thir.nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRFieldIdentity, THIRFunctionIdentity, THIRResolvedCallee,
    THIRStubCallee, THIRStubContract, THIRStubIdentity,
)
from ..thir.scalar_leaves import (
    container_element, container_view, modeled_members, native_container_subject, native_container_type,
    owned_leaf, owned_value_type, plain_record_element, readonly_elements, record_type, storage_leaf,
    view_compatible, view_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import (
    Loan, NominalType, OwnType, ReadonlyType, RefType, Representation, TpyType, VoidType, is_owned_leaf,
    is_protocol_type, loan_class,
    passing_representation, return_representation, unwrap_own, unwrap_readonly, unwrap_ref_type,
)

if TYPE_CHECKING:
    from .nodes import MIRRecordLayout

# The passings at which a parameter borrows an owned leaf the caller keeps,
# and those at which the callee receives its own copy.
BORROWING_PASSINGS = frozenset({ParamPassing.CONST_REF, ParamPassing.VIEW})
OWNING_PASSINGS = frozenset({ParamPassing.VALUE, ParamPassing.OWN})


@dataclass(frozen=True)
class MIRGlobalId:
    module: str
    name: str


@dataclass(frozen=True)
class MIRContainerStructure:
    """A native container's shape: its length, its buffer and the slots
    its iterators walk."""


@dataclass(frozen=True)
class MIRContainerElements:
    """Any element of a native container, as one region: element identity
    is never tracked, so a write of one element is a write of any."""


# A place under a parameter: fields through inline record members, then at
# most one container projection (`resolve_path` walks it).
MIRParameterPath = tuple[THIRFieldIdentity | MIRContainerStructure | MIRContainerElements, ...]


@dataclass(frozen=True)
class MIRParameterWrite:
    """A write the callee may make through parameter `parameter`: at the
    place the fields of `path` name, one inline record member after
    another, ending in at most one container projection (`(structure,)`
    grows or shrinks the container the parameter is, `(F::items,
    elements)` replaces elements of its field, `(L::a, P::x)` writes a
    scalar of the record member `a`, `(L::a,)` replaces that member)."""
    parameter: int
    path: MIRParameterPath


@dataclass(frozen=True)
class MIRReturnOrigin:
    """What a borrowed result may reach through parameter `parameter`: the
    place `path` names under it, in the write-path alphabet (`(F::items,)`
    is a container field of a record parameter, `(F::name,)` an owned-leaf
    field a view result views, `(F::line, L::a)` a record member a record
    result is). An empty path is the whole parameter, whose caller projects
    a container argument into its elements region when the result is no
    container itself."""
    parameter: int
    path: MIRParameterPath = ()


# One step of a parameter path: the record storage a field is read from and
# the field, keyed by its declaring owner (that storage or a struct-base
# ancestor of it).
MIRPathHop = tuple[NominalType, THIRFieldIdentity]


@dataclass(frozen=True)
class MIRResolvedPath:
    """A parameter path walked hop by hop (`resolve_path`): its fields with
    the storage each is read from, its trailing container projection, and
    the access the place it reaches is lent at -- readonly when the
    parameter is, or when any field on the way is `readonly[...]` typed."""
    hops: tuple[MIRPathHop, ...]
    projection: MIRContainerStructure | MIRContainerElements | None
    readonly: bool

    @property
    def fields(self) -> tuple[THIRFieldIdentity, ...]:
        return tuple(field for _, field in self.hops)


@dataclass(frozen=True)
class MIRParameterBinding:
    """How a callee binds one parameter: the type its value has in the body
    (the record, owned leaf or scalar, without access or ownership
    wrappers), the passing convention THIR published for it
    (`THIRParam.passing`), whether the callee reaches the caller's storage
    through it as a readonly borrow (False for a by-value parameter, which
    reaches nothing of the caller's), and the record a record parameter
    borrows. A stub's protocol parameter keeps its protocol type: which
    leaf it binds is the argument's fact, checked at each call."""
    type: TpyType
    passing: ParamPassing
    readonly: bool
    borrowed_record: THIRBorrowedRecord | None = None

    @property
    def protocol(self) -> bool:
        return is_protocol_type(self.type)


@dataclass(frozen=True)
class MIRCallSummary:
    """Parameter effects and global reads; environments are excluded.

    Only parameter-rooted effects are representable. Other roots require an
    explicit contract, not interpreting an absent root as an empty effect.
    Every effect is promised over ALL exits: the possible writes cover a
    prefix of the body ending in a throw, a raising nested call included,
    and nothing is retained on either exit. `normal_return_only` is False
    when the body may exit by exception. `global_reads` names the module
    globals the body (or a callee it consumes) may read; a KNOWN summary
    writes no global.

    A stub callee (`THIRStubCallee`) has no body: its summary is derived
    from the declaration alone (`stub_summary`), and its owned-leaf result
    may borrow every argument it lends (`returns`).
    """
    callee: THIRResolvedCallee | THIRStubCallee
    parameters: tuple[MIRParameterBinding, ...]
    reads: frozenset[int]
    writes: frozenset[MIRParameterWrite]
    invalidates: frozenset[int]
    returns: frozenset[MIRReturnOrigin]
    retains: frozenset[int]
    normal_return_only: bool
    global_reads: frozenset[MIRGlobalId] = frozenset()

    @property
    def borrowed_result(self) -> THIRBorrowedRecord | None:
        """What a call's result borrows through `returns`: a user callee's
        declared borrowed record or view result (`borrowed_result_of`), a
        stub's view result, or a readonly borrow of a stub's owned-leaf
        result when the stub lends it arguments; a method stub's record
        result returned by reference borrows its receiver. None for a fresh
        result."""
        if isinstance(self.callee, THIRStubCallee):
            if (view := view_result(self.callee.signature.return_type)) is not None:
                return view
            if (record := method_record_result(self.callee)) is not None:
                return record
            owned = owned_value_type(self.callee.signature.return_type)
            return THIRBorrowedRecord(owned, True) if owned is not None and self.returns else None
        return bound_result(self.callee.signature, bool(self.parameters) and self.parameters[0].readonly)

    def result_at(self, receiver_readonly: bool) -> THIRBorrowedRecord | None:
        """The borrowed result of one call whose receiver argument has
        access `receiver_readonly`: a follows-receiver result takes it, any
        other result is the summary's own."""
        if isinstance(self.callee, THIRResolvedCallee):
            return bound_result(self.callee.signature, receiver_readonly)
        return self.borrowed_result


def view_result(typ: TpyType) -> THIRBorrowedRecord | None:
    """The holder a view-returning callee hands its caller: a borrow typed
    by the view, returned as a VIEW -- a view leaf (`view_leaf`, readonly)
    or a container view whose members MIR models (`modeled_members`;
    readonly when its element argument is). None for any other result."""
    if return_representation(typ) is not Representation.VIEW:
        return None
    if view_leaf(typ):
        return THIRBorrowedRecord(typ, True)
    if container_view(typ) and modeled_members(typ):
        return THIRBorrowedRecord(typ, readonly_elements(typ))
    return None


def method_record_result(callee: THIRStubCallee) -> THIRBorrowedRecord | None:
    """The element a method stub returns by reference (`setdefault` on a
    dict of records): a borrow of its receiver's storage, with the
    receiver's declared access. None for any other result."""
    typ = callee.signature.return_type
    if not callee.receiver or return_representation(typ) is not Representation.REFERENCE:
        return None
    bare = unwrap_readonly(unwrap_ref_type(typ))
    if not plain_record_element(bare):
        return None
    return THIRBorrowedRecord(bare, callee.readonly[0] or isinstance(unwrap_ref_type(typ), ReadonlyType))


def borrowed_result_of(signature: THIRCallableSignature) -> THIRBorrowedRecord | None:
    """The borrowed result a signature returns: THIR's borrowed record,
    a view result, or a native container returned by reference (borrowed
    like a record result). One fact for the body's
    `MIRFunction.borrowed_result` and its callers' summaries."""
    if signature.borrowed_result is not None:
        return signature.borrowed_result
    return view_result(signature.return_type) or container_result(signature.return_type)


def bound_result(signature: THIRCallableSignature, receiver_readonly: bool) -> THIRBorrowedRecord | None:
    """The borrowed result a signature returns with its receiver bound at
    access `receiver_readonly`. A follows-receiver result (`@auto_readonly`:
    C++ picks the clone by the receiver's constness) is readonly when its
    return type says so or the receiver is; any other result is the
    signature's own (`borrowed_result_of`)."""
    result = borrowed_result_of(signature)
    if result is None or not signature.result_follows_receiver or view_leaf(result.type):
        return result
    declared = isinstance(unwrap_ref_type(signature.return_type), ReadonlyType) or (
        container_view(result.type) and readonly_elements(result.type))
    return THIRBorrowedRecord(result.type, declared or receiver_readonly)


def container_result(typ: TpyType) -> THIRBorrowedRecord | None:
    """The borrow a native container returned by reference hands its
    caller, readonly when its type is; None for any other result (an
    `Own[...]` container returns storage)."""
    bare = unwrap_readonly(unwrap_ref_type(typ))
    if return_representation(typ) is not Representation.REFERENCE or not native_container_type(bare):
        return None
    return THIRBorrowedRecord(bare, isinstance(unwrap_ref_type(typ), ReadonlyType))


def parameter_binding_problem(typ: TpyType, binding: 'MIRParameterBinding') -> str | None:
    """Check one binding against its declared type: the passing is the
    type's own at one of the two const verdicts, and the access agrees with
    it. None when the binding is well-formed.

    A native container binds by reference (readonly exactly at CONST_REF)
    or, as `Own[...]`, as the callee's own storage; a container view binds
    by value, readonly when its element argument is; an `Own[...]` element
    of a container (a scalar or owned leaf, or a plain record) and an
    `Own[R]` record are handed over by value or by move, the callee's own
    storage. Whether MIR models the record's definition is checked where
    definitions are known (the lowering, the summary)."""
    owned = owned_value_type(typ)
    declared = unwrap_readonly(unwrap_ref_type(typ))
    subject = native_container_subject(typ)
    container = native_container_type(subject) or container_view(subject)
    handed = isinstance(declared, OwnType) and not container and owned is None
    bare = owned if owned is not None else subject if container or handed else declared
    if (not isinstance(binding, MIRParameterBinding) or binding.type != bare
            or not isinstance(binding.passing, ParamPassing) or type(binding.readonly) is not bool):
        return "invalid call parameter binding"
    if binding.passing not in (typ.param_passing(False), typ.param_passing(True)):
        return "call parameter passing differs from its type"
    ref = binding.borrowed_record
    if ref is not None:
        # Storage handed over is the callee's own, never a borrow of the caller's.
        if (handed or not isinstance(ref, THIRBorrowedRecord) or ref.type != bare
                or not record_type(bare) or type(ref.readonly) is not bool
                or binding.readonly is not ref.readonly or binding.passing is not typ.param_passing(ref.readonly)):
            return "unsupported record call parameter"
    elif owned is not None:
        if not (binding.passing in BORROWING_PASSINGS and binding.readonly
                or binding.passing in OWNING_PASSINGS and not binding.readonly):
            return "unsupported owned-leaf call parameter"
    elif view_leaf(bare):
        # A view passed by value hands the callee the caller's loan, which it reads only.
        if not (binding.passing is ParamPassing.VALUE and binding.readonly):
            return "unsupported view call parameter"
    elif native_container_type(bare):
        if not (modeled_members(bare) and (
                binding.passing is ParamPassing.CONST_REF and binding.readonly
                or binding.passing in (ParamPassing.MUT_REF, ParamPassing.OWN) and not binding.readonly)):
            return "unsupported container call parameter"
    elif container_view(bare):
        # A container view passed by value hands the callee the caller's loan on its elements.
        if not (modeled_members(bare) and binding.passing is ParamPassing.VALUE
                and (binding.readonly or not readonly_elements(bare))):
            return "unsupported container view call parameter"
    elif handed:
        if not ((container_element(bare) or record_type(bare))
                and binding.passing in OWNING_PASSINGS and not binding.readonly):
            return "unsupported element call parameter"
    elif binding.readonly or not storage_leaf(typ, passing_representation(binding.passing)):
        return "unsupported scalar call parameter"
    return None


def owned_record_result(typ: TpyType) -> NominalType | None:
    """The record an `Own[R]` result hands the caller by value: storage the
    callee moved out, which the caller owns and destroys. A bare record
    result is a borrow, never this."""
    bare = unwrap_own(typ)
    return (bare if isinstance(typ, OwnType) and record_type(bare)
            and return_representation(typ) is Representation.STORAGE else None)


def result_problem(typ: TpyType, ref: THIRBorrowedRecord | None) -> str | None:
    if ref is None:
        # An owned leaf, an `Own[...]` container or an `Own[R]` record
        # returns by value, as storage the caller receives.
        storage = return_representation(typ) is Representation.STORAGE
        return (None if storage_leaf(typ, return_representation(typ)) or isinstance(typ, VoidType)
                or storage and (is_owned_leaf(typ) or native_container_type(native_container_subject(typ)))
                or owned_record_result(typ) is not None
                else "unsupported return type")
    if isinstance(ref, THIRBorrowedRecord) and (view_leaf(ref.type) or container_view(ref.type)):
        return None if view_result(typ) == ref else "invalid borrowed result"
    if isinstance(ref, THIRBorrowedRecord) and native_container_type(ref.type):
        # As for a record, the holder may be more readonly than the type (a
        # follows-receiver result bound at a readonly receiver).
        declared = container_result(typ)
        return (None if declared is not None and declared.type == ref.type and type(ref.readonly) is bool
                and (ref.readonly or not declared.readonly) else "invalid borrowed result")
    if (not isinstance(ref, THIRBorrowedRecord) or type(ref.readonly) is not bool
            or not isinstance(typ, (RefType, ReadonlyType))
            or not record_type(ref.type)
            or unwrap_readonly(unwrap_ref_type(typ)) != ref.type
            or isinstance(unwrap_ref_type(typ), ReadonlyType) and not ref.readonly):
        return "invalid borrowed result"
    return None


# The passings at which a protocol parameter only reads what it binds.
_STUB_PROTOCOL_PASSINGS = BORROWING_PASSINGS | {ParamPassing.VALUE, ParamPassing.TRAIT}


def stub_summary(callee: THIRStubCallee) -> MIRCallSummary | str:
    """The summary a stub's declaration supplies, or why it supplies none.

    The declared contract (`@pure` / `transient=True`) promises the bound C++
    reads or writes only its arguments, retains nothing and reaches no other
    TPy storage; admitting only readonly parameters narrows that to a
    reader. So the stub reads every parameter, writes, invalidates and
    retains nothing, reads no global and may raise. An owned-leaf result is
    not fresh by declaration -- `min(a, b)` binds `std::min`, a reference to
    an argument -- so it may borrow every argument the call lends."""
    if (not isinstance(callee, THIRStubCallee) or not isinstance(callee.identity, THIRStubIdentity)
            or not isinstance(callee.signature, THIRCallableSignature)):
        return "invalid stub callee"
    identity, signature = callee.identity, callee.signature
    if (not isinstance(identity.qualified_name, str) or not identity.qualified_name
            or identity.param_types != signature.param_types or signature.borrowed_result is not None
            or not isinstance(callee.readonly, tuple) or len(callee.readonly) != len(signature.param_types)
            or any(type(r) is not bool for r in callee.readonly)
            or type(callee.mutates_elements) is not bool or type(callee.receiver) is not bool
            or not isinstance(callee.bound_arguments, tuple)
            or not callee.receiver and (callee.mutates_elements or callee.bound_arguments)
            or callee.mutates_elements and (not callee.readonly or callee.readonly[0]
                                            or callee.contract is THIRStubContract.PURE)):
        return "invalid stub callee"
    if callee.receiver:
        return _method_stub_summary(callee)
    if callee.contract is None:
        return "stub declares no contract"
    if not isinstance(callee.contract, THIRStubContract):
        return "invalid stub callee"
    if (signature.passings is None or len(signature.passings) != len(signature.param_types)
            or signature.return_representation is None
            or signature.return_representation is not return_representation(signature.return_type)):
        return "stub signature unpublished"
    bindings: list[MIRParameterBinding] = []
    for typ, passing, readonly in zip(signature.param_types, signature.passings, callee.readonly):
        owned = owned_value_type(typ)
        if is_protocol_type(unwrap_readonly(unwrap_ref_type(typ))):
            # Dispatch through a protocol runs the argument's own dunder; only
            # a pure stub bound to a builtin leaf, checked per call, is the
            # stub's own runtime code.
            if callee.contract is not THIRStubContract.PURE:
                return "stub protocol parameter needs a pure contract"
            if passing not in _STUB_PROTOCOL_PASSINGS or not readonly:
                return "stub parameter is not a readonly leaf"
            bindings.append(MIRParameterBinding(unwrap_readonly(unwrap_ref_type(typ)), passing, True))
            continue
        # An owned leaf at any other passing lets the stub mutate, move from
        # or keep the caller's storage.
        if owned is not None and passing in BORROWING_PASSINGS and readonly:
            binding = MIRParameterBinding(owned, passing, True)
        elif view_leaf(unwrap_readonly(typ)) and passing is ParamPassing.VALUE:
            # A view lends what it views; it cannot write through it.
            binding = MIRParameterBinding(unwrap_readonly(typ), passing, True)
        elif storage_leaf(typ) and passing is ParamPassing.VALUE:
            binding = MIRParameterBinding(typ, passing, False)
        else:
            return "stub parameter is not a readonly leaf"
        problem = parameter_binding_problem(typ, binding)
        if problem is not None:
            return problem
        bindings.append(binding)
    result = signature.return_type
    representation = signature.return_representation
    lent = frozenset(MIRReturnOrigin(i) for i, b in enumerate(bindings) if b.readonly)
    if view_result(result) is not None:
        # A view result may borrow every argument the call lends, and only
        # those: with none lent its origin is outside the call.
        if not lent:
            return "stub view result has no lent origin"
        return MIRCallSummary(callee, tuple(bindings), frozenset(range(len(bindings))), frozenset(), frozenset(),
                              lent, frozenset(), False, frozenset())
    if representation in (Representation.VIEW, Representation.REFERENCE):
        return "stub view result"
    owned_result = owned_value_type(result)
    if not (isinstance(result, VoidType) or storage_leaf(result, representation)
            or owned_result is not None and representation is Representation.STORAGE):
        return "unsupported stub result type"
    returns = lent if owned_result is not None else frozenset()
    return MIRCallSummary(callee, tuple(bindings), frozenset(range(len(bindings))), frozenset(), frozenset(),
                          returns, frozenset(), False, frozenset())


def stub_protocol_argument(read: TpyType) -> bool:
    """Whether a pure stub's protocol parameter may bind an argument whose
    read type (`through_view`) is `read`: a builtin leaf (inert or owned),
    whose dispatch runs the stub's own runtime code, or a native container
    or container view whose members MIR models, which the stub reads whole
    (`len(xs)`, `len(d.values())`)."""
    if native_container_type(read) or container_view(read):
        return modeled_members(read) and not _holds_borrow(read)
    td = type_def_of(read)
    return td is not None and (td.owned_leaf or td.loan_inert)


def _holds_borrow(container: TpyType) -> bool:
    return any(loan_class(a).holds is Loan.YES for a in container.type_args if isinstance(a, TpyType))


def _method_stub_summary(callee: THIRStubCallee) -> MIRCallSummary | str:
    """The summary a native container method's declaration supplies.

    Parameter 0 is the receiver: a native container by reference or a
    container view by value. The receiver effect is derived from the
    declaration, first match wins: a `@pure` method (an `@auto_readonly`
    mutable clone included) and a `@readonly` one only read it; a method
    declaring `mutates="elements"`, and any mutating method through a view
    (which cannot change a container's shape), write its elements; any
    other method writes its structure. The other parameters follow the
    free-stub rule, plus an `Own[...]` element handed over by value or by
    move; a protocol parameter (an iterable, a key) runs code the
    declaration does not describe, so it needs the stub's own contract. A
    method whose type parameter carries a protocol bound dispatches that
    protocol on the bound type argument, which must be a builtin leaf. A
    result returned by reference or as a view borrows the receiver and
    every other lent argument; one returned by value is fresh. Every
    method may raise."""
    signature = callee.signature
    if (signature.passings is None or len(signature.passings) != len(signature.param_types)
            or signature.return_representation is None
            or signature.return_representation is not return_representation(signature.return_type)):
        return "stub signature unpublished"
    if not signature.param_types or not (callee.contract is None or isinstance(callee.contract, THIRStubContract)):
        return "invalid stub callee"
    receiver = signature.param_types[0]
    view = container_view(receiver)
    if not (native_container_type(receiver) or view):
        return "unsupported stub receiver"
    # The bound protocol runs on the elements inside the stub: a user dunder there is user code.
    if any(not (storage_leaf(b) or owned_leaf(b)) for b in callee.bound_arguments):
        return "stub protocol argument is not a builtin leaf"
    if _holds_borrow(receiver):
        return "container holds a borrow"
    if not modeled_members(receiver):
        return "unsupported native container element"
    bindings = [MIRParameterBinding(receiver, signature.passings[0], callee.readonly[0])]
    problem = parameter_binding_problem(receiver, bindings[0])
    if problem is not None:
        return problem
    for typ, passing, readonly in zip(signature.param_types[1:], signature.passings[1:], callee.readonly[1:]):
        declared = unwrap_readonly(unwrap_ref_type(typ))
        owned = owned_value_type(typ)
        if is_protocol_type(declared):
            if callee.contract is None:
                return "stub declares no contract"
            if callee.contract is not THIRStubContract.PURE:
                return "stub protocol parameter needs a pure contract"
            if passing not in _STUB_PROTOCOL_PASSINGS or not readonly:
                return "stub parameter is not a readonly leaf"
            bindings.append(MIRParameterBinding(declared, passing, True))
            continue
        if owned is not None and passing in BORROWING_PASSINGS and readonly:
            binding = MIRParameterBinding(owned, passing, True)
        elif view_leaf(declared) and passing is ParamPassing.VALUE:
            binding = MIRParameterBinding(declared, passing, True)
        elif storage_leaf(typ) and passing is ParamPassing.VALUE:
            binding = MIRParameterBinding(typ, passing, False)
        elif (isinstance(declared, OwnType) and passing in OWNING_PASSINGS
              and container_element(element := native_container_subject(typ))):
            # An element copied or moved into the container.
            binding = MIRParameterBinding(element, passing, False)
        else:
            return "stub parameter is not a readonly leaf"
        problem = parameter_binding_problem(typ, binding)
        if problem is not None:
            return problem
        bindings.append(binding)
    writes: frozenset[MIRParameterWrite] = frozenset()
    if callee.contract is not THIRStubContract.PURE and not callee.readonly[0]:
        projection = (MIRContainerElements() if callee.mutates_elements or view else MIRContainerStructure())
        writes = frozenset({MIRParameterWrite(0, (projection,))})
    result = signature.return_type
    representation = signature.return_representation
    returns: frozenset[MIRReturnOrigin] = frozenset()
    if representation in (Representation.VIEW, Representation.REFERENCE) and not isinstance(result, VoidType):
        if view_result(result) is None and method_record_result(callee) is None:
            return "unsupported stub result type"
        returns = frozenset(MIRReturnOrigin(i) for i, b in enumerate(bindings) if i == 0 or b.readonly)
    elif not isinstance(result, VoidType):
        stored = native_container_subject(result)
        if not (representation is Representation.STORAGE
                and (storage_leaf(stored) or owned_leaf(stored) or plain_record_element(stored)
                     or native_container_type(stored) and modeled_members(stored))):
            return "unsupported stub result type"
    return MIRCallSummary(callee, tuple(bindings), frozenset(range(len(bindings))), writes, frozenset(),
                          returns, frozenset(), False, frozenset())


_CONTAINER_PROJECTIONS = (MIRContainerStructure, MIRContainerElements)


def path_hops(binding: MIRParameterBinding, path: object, storage: NominalType | None = None
              ) -> tuple[MIRPathHop, ...] | None:
    """The hops of the fields leading a parameter path, in order: hop 0
    reads the record the parameter binds (`storage` when given: the record
    an argument binds at the call, which may be a descendant of the
    parameter's), hop k the record the previous field is. A container
    projection may only end the path. None when the path is malformed: a
    step that is neither a field nor a final projection, fields under a
    parameter binding no record, or a field before the last that is no
    record (a scalar, owned-leaf or container field ends the chain).

    Each field is keyed by its declaring owner, the hop's storage or one of
    its struct-base ancestors; its membership in that storage's layout is
    checked where layouts are known (the call, the summary)."""
    if not isinstance(path, tuple):
        return None
    fields = path[:-1] if path and isinstance(path[-1], _CONTAINER_PROJECTIONS) else path
    if not all(isinstance(f, THIRFieldIdentity) for f in fields):
        return None
    if not fields:
        return ()
    ref = binding.borrowed_record if isinstance(binding, MIRParameterBinding) else None
    if not isinstance(ref, THIRBorrowedRecord):
        return None
    current = storage if storage is not None else ref.type
    hops: list[MIRPathHop] = []
    for field in fields:
        if hops:
            current = unwrap_readonly(hops[-1][1].type)
            if not record_type(current):
                return None
        hops.append((current, field))
    return tuple(hops)


def resolve_path(parameter: object, path: object, parameters: tuple[MIRParameterBinding, ...]
                 ) -> MIRResolvedPath | None:
    """Walk a parameter path under the one grammar writes and return origins
    share: fields through inline record members (`path_hops`), then at most
    one container projection. The access at its end accumulates: readonly
    when the parameter is, or when any field on the way is `readonly[...]`
    typed. None when the parameter index or the path is malformed."""
    if (type(parameter) is not int or not 0 <= parameter < len(parameters)
            or not isinstance(path, tuple)):
        return None
    binding = parameters[parameter]
    hops = path_hops(binding, path)
    if hops is None:
        return None
    projection = path[-1] if path and isinstance(path[-1], _CONTAINER_PROJECTIONS) else None
    if not hops:
        return MIRResolvedPath((), projection, binding.readonly)
    readonly = binding.borrowed_record.readonly or any(isinstance(f.type, ReadonlyType) for _, f in hops)
    return MIRResolvedPath(hops, projection, readonly)


def record_field(field: THIRFieldIdentity, ref: THIRBorrowedRecord | None) -> bool:
    """Whether `field` can be a field of the record a borrowed record
    parameter binds, or of a record member reached from it: one keyed by
    its declaring owner, the hop's record or an ancestor. Its membership in
    the layout of the storage the hop reads is checked where that layout is
    known (the call, the summary)."""
    return ref is not None and record_type(field.owner) and isinstance(field.name, str) and bool(field.name)


def endpoint_admitted(binding: MIRParameterBinding, resolved: MIRResolvedPath,
                      result: THIRBorrowedRecord | None = None) -> bool:
    """Whether the place a resolved path reaches may be written (`result`
    None) or reached by the borrowed result `result`: the one endpoint
    table for summary writes and return origins.

    Under a record parameter (every field a `record_field`), a write needs
    a mutable path and ends at a scalar leaf (written in place), an owned
    leaf (its buffer replaced), a record member (replaced whole) or a
    container projection of a container field whose members MIR models. A
    return ends at no projection: a view result views an owned-leaf field
    of its family; a container or record result is a field of exactly its
    type, never lent with more access than the path lends.

    The whole parameter (no field): a write projects a mutable borrowed or
    owned container, or the elements of a writable container view (a view
    cannot change its container's shape). A return is a lent leaf a view
    result views, a container parameter whose elements or whole self the
    result is, or a record parameter the record result is."""
    if not resolved.hops:
        return _whole_parameter_endpoint(binding, resolved.projection, result)
    if not all(record_field(field, binding.borrowed_record) for _, field in resolved.hops):
        return False
    bare = unwrap_readonly(resolved.hops[-1][1].type)
    if result is None:
        if resolved.readonly:
            return False
        if resolved.projection is not None:
            return native_container_type(bare) and modeled_members(bare)
        return storage_leaf(bare) or owned_leaf(bare) or record_type(bare)
    if resolved.projection is not None:
        return False
    if view_leaf(result.type):
        return owned_leaf(bare) and view_compatible(result.type, bare)
    return ((native_container_type(bare) and modeled_members(bare) or record_type(bare))
            and bare == result.type and (result.readonly or not resolved.readonly))


def _whole_parameter_endpoint(binding: MIRParameterBinding,
                              projection: MIRContainerStructure | MIRContainerElements | None,
                              result: THIRBorrowedRecord | None) -> bool:
    if result is None:
        if projection is None or binding.readonly or binding.borrowed_record is not None:
            return False
        if native_container_type(binding.type) and modeled_members(binding.type):
            return True
        return (container_view(binding.type) and modeled_members(binding.type)
                and isinstance(projection, MIRContainerElements))
    if projection is not None:
        return False
    if view_leaf(result.type):
        # A view result borrows what a lent parameter of its family reaches.
        return binding.readonly and binding.borrowed_record is None and view_compatible(result.type, binding.type)
    if binding.borrowed_record is None and (native_container_type(binding.type) or container_view(binding.type)):
        # A container view or element result rooted in a container
        # parameter's elements is published as the whole parameter, as
        # is the container itself.
        return ((view_compatible(result.type, binding.type) if container_view(result.type)
                 else result.type in binding.type.type_args or result.type == binding.type)
                and (result.readonly or not binding.readonly))
    source = binding.borrowed_record
    # A whole record parameter is a record result of its own type; a
    # container result of a record parameter names its field.
    return not (container_view(result.type) or source is None or source.type != result.type
                or source.readonly and not result.readonly)


def binds_at(records: Mapping[NominalType, 'MIRRecordLayout'], storage: NominalType, slot: NominalType) -> bool:
    """Whether record storage of type `storage` binds at a borrowed record
    slot of type `slot`: the type itself or one of its struct-base ancestors,
    whose fields the storage contains at the same identities. `records`
    holds the layout of `storage` whenever the two differ."""
    return slot == storage or storage in records and slot in records[storage].ancestors


def return_origin_problem(origin: MIRReturnOrigin, result: THIRBorrowedRecord,
                          parameters: tuple[MIRParameterBinding, ...]) -> str | None:
    """Check one published return origin against the borrowed result it
    reaches (bound at the definition's receiver): a path of the grammar
    (`resolve_path`) whose endpoint the result may reach
    (`endpoint_admitted`)."""
    resolved = (resolve_path(origin.parameter, origin.path, parameters)
                if isinstance(origin, MIRReturnOrigin) else None)
    if resolved is None:
        return "invalid return parameter"
    if not endpoint_admitted(parameters[origin.parameter], resolved, result):
        return "unsupported return origin type or access"
    return None


def write_problem(write: MIRParameterWrite, parameters: tuple[MIRParameterBinding, ...]) -> str | None:
    """Check one published parameter write: a non-empty path of the grammar
    (`resolve_path`) whose endpoint may be written (`endpoint_admitted`). A
    readonly parameter is never written."""
    resolved = (resolve_path(write.parameter, write.path, parameters)
                if isinstance(write, MIRParameterWrite) and write.path else None)
    if resolved is None:
        return "invalid call write path"
    if not endpoint_admitted(parameters[write.parameter], resolved):
        return "unsupported call write field or access"
    return None


def summary_problem(summary: MIRCallSummary) -> str | None:
    """Validate the bounded contract without re-proving its supplying body.
    A stub summary must be exactly the one its declaration derives, so a
    hand-built summary cannot claim more than the stub declares."""
    if not isinstance(summary, MIRCallSummary):
        return "invalid call summary"
    if isinstance(summary.callee, THIRStubCallee):
        expected = stub_summary(summary.callee)
        if isinstance(expected, str):
            return expected
        return None if summary == expected else "stub summary differs from its declaration"
    if not isinstance(summary.callee, THIRResolvedCallee):
        return "invalid call summary"
    identity = summary.callee.identity
    signature = summary.callee.signature
    if (not isinstance(identity, THIRFunctionIdentity) or not identity.module or not identity.name
            or not (identity.owner is None or isinstance(identity.owner, str) and identity.owner)
            or not isinstance(signature, THIRCallableSignature)
            or not isinstance(signature.param_types, tuple) or not isinstance(summary.parameters, tuple)
            or any(not isinstance(indices, frozenset) or any(type(i) is not int for i in indices)
                   for indices in (summary.reads, summary.invalidates, summary.retains))
            or not isinstance(summary.returns, frozenset)
            or any(not isinstance(origin, MIRReturnOrigin) for origin in summary.returns)
            or not isinstance(summary.global_reads, frozenset)
            or any(not isinstance(g, MIRGlobalId) or not (isinstance(g.module, str) and g.module
                                                          and isinstance(g.name, str) and g.name)
                   for g in summary.global_reads)):
        return "invalid call summary identity or facts"
    if (result_problem(signature.return_type, borrowed_result_of(signature)) is not None
            or len(summary.parameters) != len(signature.param_types)
            or summary.reads != frozenset(range(len(summary.parameters)))
            or not isinstance(summary.writes, frozenset)
            or any((summary.invalidates, summary.retains))
            or type(summary.normal_return_only) is not bool):
        return "unsupported call summary contract"
    for typ, binding in zip(signature.param_types, summary.parameters):
        problem = parameter_binding_problem(typ, binding)
        if problem is not None:
            return problem
    # A method's callee binds its receiver first: a borrowed record of the owner.
    receiver = summary.parameters[0].borrowed_record if summary.parameters else None
    if identity.owner is not None and (receiver is None or not isinstance(receiver.type, NominalType)
                                       or receiver.type.qualified_name() != identity.owner):
        return "method summary without its receiver"
    if signature.result_follows_receiver and identity.owner is None:
        return "unsupported call summary contract"
    # A follows-receiver result is checked at the definition's own receiver.
    result = summary.borrowed_result
    if (result is None and summary.returns or result is not None and not summary.returns):
        return "missing or unexpected return origins"
    for origin in summary.returns:
        problem = return_origin_problem(origin, result, summary.parameters)
        if problem is not None:
            return problem
    for write in summary.writes:
        problem = write_problem(write, summary.parameters)
        if problem is not None:
            return problem
    # The callee's published passings and its body's bindings are one fact.
    if signature.passings is None:
        return "signature passings unpublished"
    if signature.passings != tuple(b.passing for b in summary.parameters):
        return "signature passings mismatch"
    return None


class MIRSummaryState(Enum):
    PENDING = auto()
    OPAQUE = auto()
    KNOWN = auto()


@dataclass(frozen=True)
class MIRSummaryResult:
    state: MIRSummaryState
    summary: MIRCallSummary | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        match self.state:
            case MIRSummaryState.PENDING:
                valid = self.summary is None and self.reason is None
            case MIRSummaryState.OPAQUE:
                valid = self.summary is None and bool(self.reason)
            case MIRSummaryState.KNOWN:
                valid = isinstance(self.summary, MIRCallSummary) and self.reason is None
            case _:
                valid = False
        if not valid:
            raise ValueError("inconsistent MIR summary state")

    @classmethod
    def opaque(cls, reason: str) -> 'MIRSummaryResult':
        return cls(MIRSummaryState.OPAQUE, reason=reason)
