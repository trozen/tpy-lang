"""Immutable certificates for the bounded call interface."""

from dataclasses import dataclass
from enum import Enum, auto

from ..thir.nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRFieldIdentity, THIRFunctionIdentity, THIRResolvedCallee,
    THIRStubCallee, THIRStubContract, THIRStubIdentity,
)
from ..thir.scalar_leaves import owned_leaf, owned_value_type, record_type, storage_leaf, view_compatible, view_leaf
from ..type_def_registry import ParamPassing
from ..typesys import (
    ReadonlyType, RefType, Representation, TpyType, VoidType, is_owned_leaf, is_protocol_type,
    passing_representation, return_representation, unwrap_readonly, unwrap_ref_type,
)

# The passings at which a parameter borrows an owned leaf the caller keeps,
# and those at which the callee receives its own copy.
BORROWING_PASSINGS = frozenset({ParamPassing.CONST_REF, ParamPassing.VIEW})
OWNING_PASSINGS = frozenset({ParamPassing.VALUE, ParamPassing.OWN})


@dataclass(frozen=True)
class MIRGlobalId:
    module: str
    name: str


@dataclass(frozen=True)
class MIRParameterWrite:
    parameter: int
    path: tuple[THIRFieldIdentity, ...]


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
    returns: frozenset[int]
    retains: frozenset[int]
    normal_return_only: bool
    global_reads: frozenset[MIRGlobalId] = frozenset()

    @property
    def borrowed_result(self) -> THIRBorrowedRecord | None:
        """What a call's result borrows through `returns`: a user callee's
        declared borrowed record or view result (`borrowed_result_of`), a
        stub's view result, or a readonly borrow of a stub's owned-leaf
        result when the stub lends it arguments. None for a fresh result."""
        if isinstance(self.callee, THIRStubCallee):
            if (view := view_result(self.callee.signature.return_type)) is not None:
                return view
            owned = owned_value_type(self.callee.signature.return_type)
            return THIRBorrowedRecord(owned, True) if owned is not None and self.returns else None
        return borrowed_result_of(self.callee.signature)


def view_result(typ: TpyType) -> THIRBorrowedRecord | None:
    """The holder a view-returning callee hands its caller: a readonly
    borrow typed by the view (`view_leaf`), returned as a VIEW. None for
    any other result."""
    if view_leaf(typ) and return_representation(typ) is Representation.VIEW:
        return THIRBorrowedRecord(typ, True)
    return None


def borrowed_result_of(signature: THIRCallableSignature) -> THIRBorrowedRecord | None:
    """The borrowed result a signature returns: THIR's borrowed record, or
    a view result. One fact for the body's `MIRFunction.borrowed_result`
    and its callers' summaries."""
    if signature.borrowed_result is not None:
        return signature.borrowed_result
    return view_result(signature.return_type)


def parameter_binding_problem(typ: TpyType, binding: 'MIRParameterBinding') -> str | None:
    """Check one binding against its declared type: the passing is the
    type's own at one of the two const verdicts, and the access agrees with
    it. None when the binding is well-formed."""
    owned = owned_value_type(typ)
    bare = owned if owned is not None else unwrap_readonly(unwrap_ref_type(typ))
    if (not isinstance(binding, MIRParameterBinding) or binding.type != bare
            or not isinstance(binding.passing, ParamPassing) or type(binding.readonly) is not bool):
        return "invalid call parameter binding"
    if binding.passing not in (typ.param_passing(False), typ.param_passing(True)):
        return "call parameter passing differs from its type"
    ref = binding.borrowed_record
    if ref is not None:
        if (not isinstance(ref, THIRBorrowedRecord) or ref.type != bare
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
    elif binding.readonly or not storage_leaf(typ, passing_representation(binding.passing)):
        return "unsupported scalar call parameter"
    return None


def result_problem(typ: TpyType, ref: THIRBorrowedRecord | None) -> str | None:
    if ref is None:
        # An owned leaf returns by value, as storage the caller receives.
        return (None if storage_leaf(typ, return_representation(typ)) or isinstance(typ, VoidType)
                or return_representation(typ) is Representation.STORAGE and is_owned_leaf(typ)
                else "unsupported return type")
    if isinstance(ref, THIRBorrowedRecord) and view_leaf(ref.type):
        return None if view_result(typ) == ref else "invalid borrowed result"
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
            or any(type(r) is not bool for r in callee.readonly)):
        return "invalid stub callee"
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
    lent = frozenset(i for i, b in enumerate(bindings) if b.readonly)
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
            or not isinstance(signature, THIRCallableSignature)
            or not isinstance(signature.param_types, tuple) or not isinstance(summary.parameters, tuple)
            or any(not isinstance(indices, frozenset) or any(type(i) is not int for i in indices)
                   for indices in (summary.reads, summary.invalidates,
                                   summary.returns, summary.retains))
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
    result = borrowed_result_of(signature)
    if (result is None and summary.returns or result is not None and not summary.returns):
        return "missing or unexpected return origins"
    for index in summary.returns:
        if not 0 <= index < len(summary.parameters):
            return "invalid return parameter"
        binding = summary.parameters[index]
        if view_leaf(result.type):
            # A view result borrows what a lent parameter of its family reaches.
            if not (binding.readonly and binding.borrowed_record is None
                    and view_compatible(result.type, binding.type)):
                return "unsupported return origin type or access"
            continue
        source = binding.borrowed_record
        if source is None or source.type != result.type or source.readonly and not result.readonly:
            return "unsupported return origin type or access"
    for write in summary.writes:
        if (not isinstance(write, MIRParameterWrite) or type(write.parameter) is not int
                or not 0 <= write.parameter < len(summary.parameters)
                or not isinstance(write.path, tuple) or len(write.path) != 1):
            return "invalid call write path"
        ref = summary.parameters[write.parameter].borrowed_record
        field = write.path[0]
        if (ref is None or ref.readonly or not isinstance(field, THIRFieldIdentity)
                or field.owner != ref.type or not field.name
                or not (storage_leaf(field.type) or owned_leaf(field.type))):
            return "unsupported call write field or access"
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
