"""Immutable certificates for the bounded call interface."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import Enum, auto

from ..thir.nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRFieldIdentity, THIRFunction, THIRFunctionIdentity,
    THIRParam, THIRResolvedCallee, THIRStubCallee, THIRStubContract, THIRStubIdentity,
    effective_params,
)
from ..thir.param_contract import bound_passing
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, container_element, container_view, holds_loan, modeled_members, native_container_subject,
    native_container_type, optional_record_field, owned_leaf, owned_value_type, plain_record_element,
    record_type, storage_leaf, value_class, view_compatible, view_endpoint, view_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import (
    NominalType, OwnType, Representation, TpyType, VoidType, is_owned_leaf,
    is_protocol_type, passing_representation, return_representation, unwrap_own, unwrap_readonly, unwrap_ref_type,
)
from .shapes import (
    MIRContainerElements, MIRContainerStructure, MIRHop, MIRTupleIndex, MIRUnionPayload, MIRWalk, Projection,
    REGION_STEPS, MIRDeref, distinct_lent_objects, lent_member, lent_members, lent_prefix, object_path,
    object_selector,
    replacement_reach,
    store_problem, walk,
)
from .value import (
    AGGREGATE_CLASSES, WRAPPER_CLASSES, Declared, MIRField, MIRFieldId, MIRHolding,
    MIRRecordLayout, MIRRole, MIRShape, MIRValidationError, MIRRecords, Passing, admission_problem, lent_shape_problem,
    handed_type, holding_of, laid_value, layout_fact, lent_record, loan_access, shape_problem,
    subject_class, value_shape,
)

# The passings at which a parameter borrows an owned leaf the caller keeps,
# and those at which the callee receives its own copy.
BORROWING_PASSINGS = frozenset({ParamPassing.CONST_REF, ParamPassing.VIEW})
OWNING_PASSINGS = frozenset({ParamPassing.VALUE, ParamPassing.OWN})


# MIR's types are bare: which position borrows is its passing or its
# published return representation. THIR still spells a borrowed parameter
# or result `Ref[...]`; each node kind's types are read through one
# accessor, which strips it.
def param_type(p: THIRParam) -> TpyType:
    return unwrap_ref_type(p.type)


def signature_params(signature: THIRCallableSignature) -> tuple[TpyType, ...]:
    return tuple(map(unwrap_ref_type, signature.param_types))


def signature_result(signature: THIRCallableSignature) -> TpyType:
    return unwrap_ref_type(signature.return_type)


def body_result(fn: THIRFunction) -> TpyType:
    return unwrap_ref_type(fn.return_type)


@dataclass(frozen=True)
class MIRGlobalId:
    module: str
    name: str


# A place under a parameter: any walk `shapes.step` takes from the object
# the parameter lends -- fields, container regions, tuple members, a sum's
# payload (`resolve_path` walks it).
MIRParameterPath = tuple[THIRFieldIdentity | MIRContainerStructure | MIRContainerElements | MIRTupleIndex
                         | MIRUnionPayload, ...]


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
class MIRAliasObligation:
    """A no-alias condition the callee's body is correct under: while the
    callee still uses the place `path` names under parameter `parameter`
    (a loan it holds, an iteration over it), it may replace `written`
    through another parameter. Each call MIR analyzes discharges it by
    checking that the two arguments do not reach overlapping storage; the
    body keeps it as an `alias_obligation` conflict, since other callers
    are not checked."""
    parameter: int
    path: MIRParameterPath
    written: MIRParameterWrite


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


@dataclass(frozen=True)
class MIRResultHolder:
    """The record an owned-record result hands the caller, as the holder
    of a loan transfer."""


@dataclass(frozen=True)
class MIRStaticLoan:
    """A literal's static storage, as the source of a loan transfer."""


MIR_RESULT = MIRResultHolder()
MIR_STATIC = MIRStaticLoan()


@dataclass(frozen=True)
class MIRLoanTransfer:
    """A loan the callee may store in an object the caller reaches: the
    view member `path` names (fields through inline record members, ending
    at a view member) under parameter `holder`, or under the record an
    owned-record result hands over (`MIR_RESULT`), may hold the loan
    `source` names -- parameter `source`'s own (the view it holds, the
    owned leaf it lends; `source_path` empty) or the loan a view member
    of it stores (`source_path`), or a literal's static storage
    (`MIR_STATIC`). A may-effect: the caller joins it with what the
    member held; a result member holds exactly its transfers."""
    holder: 'int | MIRResultHolder'
    path: MIRParameterPath
    source: 'int | MIRStaticLoan'
    source_path: MIRParameterPath = ()


@dataclass(frozen=True)
class MIRProtocolParameter:
    """A stub's protocol parameter: no value MIR models until an argument
    binds it (the argument's fact, checked at each call); read only."""
    readonly: bool


class MIRBindingContext(Enum):
    """Who binds a parameter without declaring its access: a function body
    or summary, a constructor's included."""
    FUNCTION = auto()


# The one context input of a binding's derivation: a function's, or a
# stub's (its declared access, which is also the loan's access source).
BindingContext = MIRBindingContext | Declared
FUNCTION = MIRBindingContext.FUNCTION


@dataclass(frozen=True)
class MIRParameterBinding:
    """How a callee binds one parameter (`derived_binding` derives it,
    `parameter_contract_problem` checks it): its declared type, with the
    `Own[...]` / `readonly[...]` wrapper its value's type drops; the value
    the callee holds (`value_shape`), or a protocol parameter's; the
    passing THIR published; whether the object is handed over for the
    callee alone (`private_owned`)."""
    declared: TpyType
    value: MIRShape | MIRProtocolParameter
    passing: ParamPassing
    private_owned: bool

    @property
    def protocol(self) -> bool:
        return isinstance(self.value, MIRProtocolParameter)

    @property
    def cls(self) -> MIRClass | None:
        return None if self.protocol else self.value.cls

    @property
    def type(self) -> TpyType:
        """The value's type (a protocol parameter's protocol)."""
        return native_container_subject(self.declared) if self.protocol else self.value.type

    @property
    def storage(self) -> bool:
        return parameter_storage(self.passing, self.cls)

    @property
    def lent_record(self) -> MIRShape | None:
        """The record a record binding lends (a receiver's); a wrapper's
        lent objects are its members (`lent_members`)."""
        return None if self.protocol else lent_record(self.value)

    @property
    def lent_members(self) -> tuple[tuple[MIRTupleIndex | MIRUnionPayload, MIRShape], ...]:
        """The objects a wrapper binding lends, one per pointer member."""
        return () if self.protocol else lent_members(self.value)

    def object_selector(self, path: tuple[object, ...]) -> tuple[object, ...] | None:
        """The leading step of a summary path under this binding that names
        the object it starts in (`shapes.object_selector`); nothing for a
        protocol binding."""
        return () if self.protocol else object_selector(self.value, path)

    def lent_object(self, path: tuple[object, ...]) -> MIRShape | None:
        """The pointer at the object a summary path under this binding
        starts in: the record a record binding lends, or the wrapper member
        the path's first selector names (`object_selector`)."""
        if self.protocol:
            return None
        if self.lent_record is not None:
            return self.lent_record
        selector = self.object_selector(path)
        return lent_member(self.value, selector[0]) if selector else None

    @property
    def readonly(self) -> bool:
        """Whether the caller's storage is lent readonly: the lent record's
        access, every lent member's for a wrapper, else the value's; False
        for the callee's own storage."""
        if self.storage:
            return False
        record = self.lent_record
        if record is None and (members := self.lent_members):
            return all(m.readonly for _, m in members)
        return (record if record is not None else self.value).readonly

    @property
    def scalar_value(self) -> bool:
        """The callee's own scalar value: an inert leaf at the passing THIR
        published for it, not an element handed over."""
        return self.cls is MIRClass.LEAF and not self.private_owned


def region_view(typ: TpyType) -> bool:
    """A container view MIR holds as the elements region it views: one
    whose stub declares an element a cursor binds (a Span, a dict keys or
    values view, a varargs pack). An items view is only a call's result."""
    bare = unwrap_readonly(typ)
    return isinstance(bare, NominalType) and container_view(bare) and binds_cursor(bare)


def parameter_storage(passing: ParamPassing, cls: MIRClass | None) -> bool:
    """Whether a parameter of class `cls` passed at `passing` is the
    callee's own storage: passed by value, unless a loan whatever its role
    (a view, a protocol parameter)."""
    return passing in OWNING_PASSINGS and holding_of(cls, MIRRole.STORAGE) is MIRHolding.VALUE


def _parameter_class(typ: TpyType) -> tuple[bool, MIRClass | None]:
    """Whether a parameter of type `typ` is a protocol parameter, and the
    value class of its subject (None for a protocol, or a type MIR does not
    model)."""
    if is_protocol_type(unwrap_readonly(typ)):
        return True, None
    return False, subject_class(typ)


def parameter_binding(p: THIRParam, context: BindingContext = FUNCTION) -> MIRParameterBinding | str:
    """How a callee binds parameter `p` in `context` -- a body's parameter
    or receiver, a summary's, a constructor's, a stub's -- by the one
    derivation (`derived_binding`) from its declared type, its passing and
    the wrapper THIR's one layout fact lays out (`layout_fact`,
    `laid_value`), checked by the contract (`parameter_contract_problem`).
    The passing is the parameter contract's (`param_contract.bound_passing`).
    Else the reason; what a stub takes is its caller's to check
    (`_stub_binding`)."""
    passing = bound_passing(p)
    if passing is None:
        return "unpublished parameter passing"
    declared = param_type(p)
    fact = layout_fact(p)
    laid = fact if fact is None or isinstance(fact, str) else laid_value(declared, fact)
    if isinstance(laid, str):
        return laid
    binding = derived_binding(declared, passing, laid, context)
    if isinstance(binding, str):
        return binding
    return ((not binding.protocol
             and shape_problem(binding.value, declared, representation=passing_representation(passing)))
            or binding)


def bound_params(fn: THIRFunction) -> tuple[THIRParam, ...] | None:
    """The parameters callers of `fn` bind (`effective_params`: a method's
    receiver first), at the types its signature declares (an explicit
    `@readonly` body reads `readonly[P]`, callers bind `P`); None if it
    declares no others."""
    params = effective_params(fn)
    types = signature_params(fn.resolved_callee.signature) if fn.resolved_callee is not None else None
    if types is None or tuple(map(unwrap_readonly, types)) != tuple(unwrap_readonly(param_type(p)) for p in params):
        return None
    return tuple(replace(p, type=t) for p, t in zip(params, types))


def derived_binding(declared: TpyType, passing: ParamPassing, laid: MIRShape | None,
                    context: BindingContext = FUNCTION) -> MIRParameterBinding | str:
    """The one derivation of a binding from its declared type, passing,
    context and the wrapper a fact lays out (`laid`: THIR's, or the one a
    binding holds; of the declared type's class); else the reason. The class is the subject type's (a protocol
    parameter has none); passed by value the callee's own storage
    (`parameter_storage`), private to it when handed over; else the
    caller's loan at the access its source gives
    (`loan_access`: a stub's declaration, else its passing), an Optional
    record's its payload's. A fact lays out only a wrapper (`takes_layout`);
    a record or container is admitted by its type (`admission_problem`). The passing is its type's (`_passing_problem`,
    checked first: it decides the refusal), the holding one the class
    takes (`_holding_problem`).
    Outside a stub the binding is a function's or a constructor's: no
    protocol parameter, no scalar element handed over, a record or
    container handed over only as exactly `Own[T]` at OWN (`T&&`; a
    value-type record is passed by value). What the value is checked against is the contract's
    (`parameter_contract_problem`)."""
    protocol, cls = _parameter_class(declared)
    if cls is None and not protocol:
        return "unsupported parameter type"
    if laid is not None and laid.cls is not cls:
        return "conflicting layout facts"
    stub = isinstance(context, Declared)
    storage = parameter_storage(passing, cls)
    handed = storage and handed_type(declared)
    readonly = not storage and loan_access(declared, cls, context if stub else Passing(passing))
    lent = cls in AGGREGATE_CLASSES and not storage
    if (problem := _passing_problem(declared, passing, readonly if lent else None)) is not None:
        return problem
    # A fact lays out exactly a wrapper.
    if (laid is not None) is not takes_layout(cls):
        return "conflicting layout facts" if laid is not None else "unsupported parameter type"
    if (problem := _holding_problem(cls, declared, passing, storage, handed, laid)) is not None:
        return problem
    subject = native_container_subject(declared)
    if protocol:
        value: MIRShape | MIRProtocolParameter | str = MIRProtocolParameter(readonly)
    elif laid is not None:
        value = laid
    elif (problem := admission_problem(subject, cls)) is not None:
        return problem
    else:
        value = value_shape(subject, cls, access=readonly, role=MIRRole.STORAGE if storage else MIRRole.LOAN)
    # An aggregate is handed over as exactly `Own[T]` at OWN (`T&&`).
    if not stub and (protocol or handed and (cls is MIRClass.LEAF or cls in AGGREGATE_CLASSES and not (
            passing is ParamPassing.OWN and isinstance(declared, OwnType) and declared.wrapped == value.type))):
        return "unsupported parameter type"
    return MIRParameterBinding(declared, value, passing, handed)


def _holding_problem(cls: MIRClass | None, typ: TpyType, passing: ParamPassing, storage: bool, handed: bool,
                     laid: MIRShape | None) -> str | None:
    """How each class is held beyond its type's passing (`_passing_problem`),
    `laid` its laid-out value: a scalar leaf by value, or handed over (to a
    container); a record or container lent, or handed over; a leaf view or
    a region view (`region_view`), never declared handed over. A wrapper is
    held as its type's passing and its members say (`shape_problem`)."""
    match cls:
        case MIRClass.LEAF:
            if not (handed or storage and passing is ParamPassing.VALUE and typ == native_container_subject(typ)):
                return "unsupported parameter type"
        case _ if cls in AGGREGATE_CLASSES:
            if not handed and storage:
                return "unsupported reference parameter passing"
        case MIRClass.VIEW | MIRClass.CONTAINER_VIEW:
            if handed_type(typ):
                return "unsupported parameter type"
            if cls is MIRClass.CONTAINER_VIEW and not region_view(typ):
                return "view of container storage"
    return None


def takes_layout(cls: MIRClass | None) -> bool:
    """Whether a binding of class `cls` holds a value a fact lays out: a
    wrapper, whose per-member forms its type does not state."""
    return cls in WRAPPER_CLASSES


def parameter_contract_problem(binding: object, context: BindingContext = FUNCTION) -> str | None:
    """Whether `binding` is the one `derived_binding` gives its own inputs
    in `context` -- declared type, passing, and what a fact would lay out
    of its value (`takes_layout`: an Optional record payload's access only
    the binding carries) -- its value of the declared type's class and of
    an intrinsic form (`shape_problem`, at the passing's representation;
    checked here, since equality takes an access 1 for True); None when it
    is, else the refusal."""
    if (not isinstance(binding, MIRParameterBinding) or not isinstance(binding.passing, ParamPassing)
            or not isinstance(binding.value, (MIRShape, MIRProtocolParameter))
            or type(binding.value.readonly) is not bool or type(binding.private_owned) is not bool):
        return "malformed call parameter binding"
    declared, passing, value = binding.declared, binding.passing, binding.value
    if binding.cls is not _parameter_class(declared)[1]:
        return "value class mismatch"
    if not binding.protocol and (problem := shape_problem(
            value, declared, representation=passing_representation(passing))) is not None:
        return problem
    laid = value if takes_layout(binding.cls) else None
    derived = derived_binding(declared, passing, laid, context)
    if isinstance(derived, str):
        return derived
    return None if derived == binding else "call parameter binding differs from its derivation"


@dataclass(frozen=True)
class MIRCallSummary:
    """Parameter effects and global reads; environments are excluded.

    Only parameter-rooted effects are representable. Other roots require an
    explicit contract, not interpreting an absent root as an empty effect.
    Every effect is promised over ALL exits: the possible writes cover a
    prefix of the body ending in a throw, a raising nested call included,
    and the loans the callee may store in objects the caller reaches are
    its `transfers`, on either exit. `normal_return_only` is False
    when the body may exit by exception. `global_reads` names the module
    globals the body (or a callee it consumes) may read; a KNOWN summary
    writes no global. `obligations` are the no-alias conditions its
    arguments must meet (`MIRAliasObligation`), each checked at the call.

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
    transfers: frozenset[MIRLoanTransfer]
    normal_return_only: bool
    global_reads: frozenset[MIRGlobalId] = frozenset()
    obligations: frozenset[MIRAliasObligation] = frozenset()

    @property
    def borrowed_result(self) -> THIRBorrowedRecord | None:
        """What a call's result borrows through `returns` (`call_result`):
        a user callee's at the access its receiver binding has, a stub's at
        a mutable receiver place (a readonly receiver binding does not make
        a stub's view result readonly). None for a fresh result."""
        resolved = isinstance(self.callee, THIRResolvedCallee)
        return call_result(self.callee, resolved and bool(self.parameters) and self.parameters[0].readonly,
                           lends=bool(self.returns))

    def result_at(self, receiver_readonly: bool) -> THIRBorrowedRecord | None:
        """The borrowed result of one call whose receiver argument has
        access `receiver_readonly` (`call_result`)."""
        return call_result(self.callee, receiver_readonly, lends=bool(self.returns))


def call_result(callee: THIRResolvedCallee | THIRStubCallee | None, receiver_readonly: bool | None, *,
                lends: bool = False, declared: TpyType | None = None,
                representation: Representation | None = None) -> THIRBorrowedRecord | None:
    """What one call of `callee` returns borrowed, its receiver at access
    `receiver_readonly` -- or, None, the access the signature publishes,
    no receiver adjusting it (the body's `MIRFunction.borrowed_result` and
    its callers' summaries) -- the one result path, the callee's kind an input.
    Whether the result borrows is its `representation`: a body's own
    lowering passes the one it decided (`MIRFunction.return_representation`),
    any other caller reads the signature's.
    A body THIR resolved no callee for (an overload) is a user callee
    declaring `declared`, with no receiver it follows: a view
    (`view_result`); a record or container returned by reference, a user
    callee's or a method stub's (`aggregate_result`);
    a readonly borrow of a stub's owned-leaf result when the stub lends it
    arguments (`lends`). A result borrowing the receiver takes the
    receiver's access too: a method stub's view or aggregate result,
    whatever access the stub binds the receiver at (an `@auto_readonly` pure
    clone binds it mutably; an aggregate is readonly too where the stub
    binds the receiver readonly), and a user callee's result following its receiver
    (`@auto_readonly`: C++ picks the clone by the receiver's constness).
    None for a fresh result."""
    signature = callee.signature if callee is not None else None
    typ = signature_result(signature) if signature is not None else declared
    if representation is None and signature is not None:
        representation = signature.return_representation
    stub = isinstance(callee, THIRStubCallee)
    follows = signature is not None and receiver_readonly is not None and (
        callee.receiver if stub else signature.result_follows_receiver)
    receiver = bool(follows and receiver_readonly)
    if (view := view_result(typ, representation, receiver)) is not None:
        return view
    # A method stub binds its receiver at its declared access, which an
    # aggregate it returns by reference takes too. A receiverless stub's
    # reference result lends from no receiver MIR binds: it derives none.
    if (not stub or callee.receiver) and (aggregate := aggregate_result(
            typ, representation, receiver or stub and callee.readonly[0])) is not None:
        return aggregate
    owned = owned_value_type(typ)
    if not stub or owned is None or not lends:
        return None
    return THIRBorrowedRecord(owned, loan_access(owned, MIRClass.OWNED_LEAF, None))


def view_result(typ: TpyType, representation: Representation | None,
                receiver_readonly: bool = False) -> THIRBorrowedRecord | None:
    """The holder a view-returning callee hands its caller: a borrow typed
    by the view, returned as a VIEW (its published `representation`) -- a
    view leaf (`view_leaf`, readonly) or a container view whose members
    MIR models (`modeled_members`; readonly when its element argument is,
    or when it views a receiver bound readonly, `receiver_readonly`).
    None for any other result."""
    if representation is not Representation.VIEW:
        return None
    if view_leaf(typ):
        return THIRBorrowedRecord(typ, loan_access(typ, MIRClass.VIEW, None))
    if container_view(typ) and modeled_members(typ):
        return THIRBorrowedRecord(typ, loan_access(typ, MIRClass.CONTAINER_VIEW, Declared(receiver_readonly)))
    return None


def aggregate_result(typ: TpyType, representation: Representation | None,
                     receiver_readonly: bool = False) -> THIRBorrowedRecord | None:
    """The borrow a record or native container returned by reference (its
    published `representation`) hands its caller, one derivation for both:
    of its declared type, readonly when the type is or it borrows a
    receiver bound readonly (`receiver_readonly`); None for any other
    result (an `Own[...]` aggregate returns storage) and for an aggregate
    MIR does not model (`admission_problem`)."""
    bare = unwrap_readonly(typ)
    cls = value_class(bare)
    if (representation is not Representation.REFERENCE or cls not in AGGREGATE_CLASSES
            or admission_problem(bare, cls) is not None):
        return None
    return THIRBorrowedRecord(bare, loan_access(typ, cls, Declared(receiver_readonly)))


def _passing_problem(typ: TpyType, passing: ParamPassing, loan_readonly: bool | None = None) -> str | None:
    """A passing is the type's own at one of the two const verdicts; a
    loan of a record or container's at its access (`loan_readonly`: a
    stub's declaration, not its passing, decides a stub loan's)."""
    verdicts = (False, True) if loan_readonly is None else (loan_readonly,)
    if passing not in {typ.param_passing(readonly) for readonly in verdicts}:
        return "call parameter passing differs from its type"
    return None


def owned_aggregate_result(typ: TpyType) -> NominalType | None:
    """The record or container an `Own[...]` result hands the caller by
    value: storage the callee moved out, which the caller owns and
    destroys. A bare aggregate result is a borrow, never this."""
    bare = unwrap_own(typ)
    return (bare if isinstance(typ, OwnType) and value_class(bare) in AGGREGATE_CLASSES
            and return_representation(typ) is Representation.STORAGE else None)


def result_problem(typ: TpyType, ref: THIRBorrowedRecord | None, representation: Representation | None) -> str | None:
    """Whether a result of type `typ` at its published `representation`
    is the borrowed result `ref` (None for a fresh one) describes; else the
    refusal."""
    if ref is None:
        # An owned leaf or an `Own[...]` aggregate returns by value, as
        # storage the caller receives.
        storage = representation is Representation.STORAGE
        return (None if representation is not None and storage_leaf(typ, representation) or isinstance(typ, VoidType)
                or storage and is_owned_leaf(typ)
                or owned_aggregate_result(typ) is not None
                else "unsupported return type")
    if isinstance(ref, THIRBorrowedRecord) and (view_leaf(ref.type) or container_view(ref.type)):
        return None if view_result(typ, representation) == ref else "invalid borrowed result"
    # A record or container returned by reference: its holder, which may be
    # more readonly than the type (a follows-receiver result bound at a
    # readonly receiver), checked once (`lent_shape_problem`).
    if aggregate_result(typ, representation) is None or lent_shape_problem(typ, ref) is not None:
        return "invalid borrowed result"
    return None


# The passings at which a protocol parameter only reads what it binds.
_STUB_PROTOCOL_PASSINGS = BORROWING_PASSINGS | {ParamPassing.VALUE, ParamPassing.TRAIT}


def _stub_binding(callee: THIRStubCallee, typ: TpyType, passing: ParamPassing, readonly: bool, *,
                  method: bool) -> MIRParameterBinding | str:
    """A stub's non-receiver parameter (`parameter_binding` at its declared
    access `readonly`), admitted only where the declaration says what the
    stub does with it: a protocol parameter of a pure stub, read only
    (dispatch through it runs the argument's own dunder; only a pure stub
    bound to a builtin leaf, checked per call, is the stub's own runtime
    code); an owned leaf lent readonly (at any other passing the stub could
    mutate, move from or keep the caller's storage); a view (it lends what
    it views and cannot write through it); a scalar by value; and, at a
    method, an element handed over to the container."""
    binding = parameter_binding(THIRParam("", typ, passing=passing), Declared(readonly))
    if isinstance(binding, str):
        return "stub parameter is not a readonly leaf"
    if binding.protocol:
        if method and callee.contract is None:
            return "stub declares no contract"
        if callee.contract is not THIRStubContract.PURE:
            return "stub protocol parameter needs a pure contract"
        if passing not in _STUB_PROTOCOL_PASSINGS or not readonly:
            return "stub parameter is not a readonly leaf"
        return binding
    # A view lends its loan whatever the stub declares; an owned leaf only where it declares a reader.
    if not (binding.value.leaf_borrow and (readonly or binding.cls is MIRClass.VIEW)
            or binding.scalar_value and passing is ParamPassing.VALUE
            or method and binding.private_owned and container_element(binding.type)):
        return "stub parameter is not a readonly leaf"
    return binding


def stub_summary(callee: THIRStubCallee) -> MIRCallSummary | str:
    """The summary a stub's declaration supplies, or why it supplies none.

    The declared contract (`@pure` / `transient=True`) promises the bound C++
    reads or writes only its arguments, retains nothing and reaches no other
    TPy storage; admitting only readonly parameters narrows that to a
    reader. So the stub reads every parameter, writes, invalidates and
    retains nothing, reads no global and may raise. An owned-leaf result is
    not fresh by declaration -- `min(a, b)` binds `std::min`, a reference to
    an argument -- so it may borrow every argument the call lends. A
    constructed result (`constructs_result`, an initializer) is the
    exception: the call builds a new value, so it borrows no argument."""
    if (not isinstance(callee, THIRStubCallee) or not isinstance(callee.identity, THIRStubIdentity)
            or not isinstance(callee.signature, THIRCallableSignature)):
        return "invalid stub callee"
    identity, signature = callee.identity, callee.signature
    if (not isinstance(identity.qualified_name, str) or not identity.qualified_name
            or identity.param_types != signature.param_types
            or not isinstance(callee.readonly, tuple) or len(callee.readonly) != len(signature.param_types)
            or any(type(r) is not bool for r in callee.readonly)
            or type(callee.mutates_elements) is not bool or type(callee.receiver) is not bool
            or type(callee.constructs_result) is not bool or callee.constructs_result and callee.receiver
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
            or signature.return_representation is not return_representation(signature_result(signature))):
        return "stub signature unpublished"
    bindings: list[MIRParameterBinding] = []
    for typ, passing, readonly in zip(signature_params(signature), signature.passings, callee.readonly):
        binding = _stub_binding(callee, typ, passing, readonly, method=False)
        if isinstance(binding, str):
            return binding
        bindings.append(binding)
    result = signature_result(signature)
    representation = signature.return_representation
    lent = frozenset(MIRReturnOrigin(i) for i, b in enumerate(bindings) if b.readonly)
    if view_result(result, representation) is not None:
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
    returns = lent if owned_result is not None and not callee.constructs_result else frozenset()
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
    return any(holds_loan(a) for a in container.type_args)


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
            or signature.return_representation is not return_representation(signature_result(signature))):
        return "stub signature unpublished"
    if not signature.param_types or not (callee.contract is None or isinstance(callee.contract, THIRStubContract)):
        return "invalid stub callee"
    receiver = signature_params(signature)[0]
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
    receiver_binding = parameter_binding(THIRParam("self", receiver, passing=signature.passings[0]),
                                         Declared(callee.readonly[0]))
    if isinstance(receiver_binding, str):
        return receiver_binding
    bindings = [receiver_binding]
    for typ, passing, readonly in zip(signature_params(signature)[1:], signature.passings[1:], callee.readonly[1:]):
        binding = _stub_binding(callee, typ, passing, readonly, method=True)
        if isinstance(binding, str):
            return binding
        bindings.append(binding)
    writes: frozenset[MIRParameterWrite] = frozenset()
    if callee.contract is not THIRStubContract.PURE and not callee.readonly[0]:
        projection = (MIRContainerElements() if callee.mutates_elements or view else MIRContainerStructure())
        writes = frozenset({MIRParameterWrite(0, (projection,))})
    result = signature_result(signature)
    representation = signature.return_representation
    returns: frozenset[MIRReturnOrigin] = frozenset()
    if representation in (Representation.VIEW, Representation.REFERENCE) and not isinstance(result, VoidType):
        if call_result(callee, None) is None:
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


def path_step(item: object) -> Projection:
    """One item of a summary's parameter path as a MIR projection."""
    match item:
        case THIRFieldIdentity(owner=owner, name=name, type=typ):
            return MIRField(MIRFieldId(owner, name), typ)
        case MIRContainerStructure() | MIRContainerElements() | MIRTupleIndex() | MIRUnionPayload():
            return item
    raise MIRValidationError("unknown call path item")


def summary_path(projections: tuple[Projection, ...]) -> MIRParameterPath:
    """A place's projections under a parameter, spelled as a summary path
    (`path_step` reads it back): each field by its THIR identity, any
    other step as it is (the walk refuses what no step admits); a
    dereference is implied (`object_path`, the inverse)."""
    return tuple(THIRFieldIdentity(p.id.owner, p.id.name, p.type) if isinstance(p, MIRField) else p
                 for p in projections if not isinstance(p, MIRDeref))


def path_projections(shape: MIRShape, path: tuple[object, ...]) -> tuple[Projection, ...]:
    """The projections a summary path names under a value of `shape`
    (`object_path`: each step of the path, `path_step`, with the
    dereferences of the pointers it reaches)."""
    return object_path(shape, tuple(map(path_step, path)))


# The walks a summary's checks made, by the end (a parameter index or the
# owned-record result) and the path they walked: what a call reads its
# caller-dependent part off (`crossed_records`) instead of walking again.
MIRPathWalks = dict[tuple[object, MIRParameterPath], MIRWalk]


def _path_walk(end: object, path: object, root: MIRShape, layouts: MIRRecords,
               walks: MIRPathWalks | None) -> MIRWalk | str | None:
    """Summary path `path` under `end` walked from a value of `root`
    (`path_projections`, the one stepping table `shapes.step`; a trailing
    region is terminal, `walk`'s `region_end`), recorded in `walks` when it
    holds and crosses a field. None when the path is malformed; the walk's
    refusal when a hop is refused."""
    if not isinstance(path, tuple):
        return None
    try:
        projections = path_projections(root, path)
    except MIRValidationError:
        return None
    try:
        walked = walk(root, projections, layouts, region_end=True)
    except MIRValidationError as refusal:
        return str(refusal)
    if walks is not None and any(isinstance(p, MIRField) for p in projections):
        walks[end, path] = walked
    return walked


def path_walk(root: MIRShape, path: tuple[object, ...], layouts: MIRRecords) -> MIRWalk | str | None:
    """`path` walked from a value of `root` (`_path_walk`), recording nothing."""
    return _path_walk(None, path, root, layouts, None)


def path_hops(binding: MIRParameterBinding, walked: MIRWalk) -> tuple[MIRHop, ...] | None:
    """The hops of a parameter walk inside the object the parameter lends
    (`lent_prefix`): the path's own. None when the walk enters no lent
    object -- a container view's region is its own object, so all its hops."""
    if binding.value.container_view_holder:
        return walked.hops
    prefix = lent_prefix(binding.value, tuple(hop.projection for hop in walked.hops))
    return None if prefix is None else walked.hops[len(prefix):]


def resolve_path(parameter: object, path: object, parameters: tuple[MIRParameterBinding, ...],
                 layouts: MIRRecords, walks: MIRPathWalks | None = None) -> MIRWalk | str | None:
    """A parameter path walked from the value the parameter binds
    (`_path_walk`). None when the parameter index or the path is malformed
    (any path under a protocol parameter included); the walk's refusal when
    a hop is refused."""
    if type(parameter) is not int or not 0 <= parameter < len(parameters):
        return None
    binding = parameters[parameter]
    if not isinstance(binding, MIRParameterBinding) or binding.protocol:
        return None
    return _path_walk(parameter, path, binding.value, layouts, walks)


def _under_optional_field_payload(path: tuple[object, ...]) -> bool:
    """Whether a summary path steps into an Optional record field's payload."""
    return any(isinstance(item, MIRUnionPayload) and isinstance(previous, THIRFieldIdentity)
               and optional_record_field(previous.type) is not None
               for previous, item in zip(path, path[1:]))


def return_endpoint_problem(path: tuple[object, ...], end: Callable[[], TpyType | None],
                            result: TpyType) -> str | None:
    """The one rule for where a published borrowed return ends, read by the
    summary (`endpoint_admitted`) and by each caller
    (`dependencies.call_return_problem`), so no summary is known whose
    result every caller refuses. `end` gives the type the path reaches,
    asked only for a path the rule does not refuse by its steps.

    A return ends at no region and never at or under an Optional record
    field's payload: a caller's write through it would replace the whole
    field (`whole_wrapper_field`). A view result views an owned leaf or is
    the loan a view member stores; a container or record result is a place
    of exactly its type. The parameter itself (no path) is the result, or a
    container parameter holding it in its elements region (the caller
    projects there): what it lends is checked by each side against its own
    binding or argument."""
    if path and isinstance(path[-1], REGION_STEPS):
        return "call result of a container region"
    if _under_optional_field_payload(path):
        return "call result under an Optional field's payload"
    if not path:
        return None
    bare = unwrap_readonly(end())
    if view_leaf(result):
        return None if view_endpoint(result, bare) else "view result of a non-leaf place"
    if bare != result or not (record_type(bare) or native_container_type(bare) and modeled_members(bare)):
        return "call result of a mismatched place"
    return None


def endpoint_admitted(binding: MIRParameterBinding, walked: MIRWalk,
                      result: THIRBorrowedRecord | None = None) -> bool:
    """Whether the place a parameter walk reaches may be written (`result`
    None) or reached by the borrowed result `result`: one rule over the
    walk, whatever kinds the path stepped through.

    Only what the parameter lends lies in caller storage: a holder's object
    or a view's region, never storage handed over or a value. A write takes
    at least one step, passes TPy's write rule (`store_problem`: a view
    never changes its container's shape) and ends where a write replaces
    storage keeping the object around it (`replacement_reach`: a
    container's structure or elements, an owned member field) or at a
    scalar leaf written in place. A return meets the return endpoint rule
    (`return_endpoint_problem`), never lent with more access than the path
    lends; the parameter itself is a record result of its own type, or for
    a container an element or view of its elements or itself."""
    steps = path_hops(binding, walked)
    if steps is None:
        # Nothing lent: only the parameter itself is a result, of a binding
        # the cases below check.
        if result is None or walked.hops:
            return False
        steps = ()
    projections = tuple(hop.projection for hop in steps)
    if result is None:
        if not steps or store_problem(walked) is not None:
            return False
        return replacement_reach(projections) is not None or walked.end.cls is MIRClass.LEAF
    end = walked.end
    if return_endpoint_problem(summary_path(projections), lambda: None if end is None else end.type, result.type):
        return False
    if steps:
        return view_leaf(result.type) or result.readonly or not end.readonly
    if view_leaf(result.type):
        return binding.value.leaf_borrow and view_compatible(result.type, binding.type)
    if binding.cls in (MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW):
        return ((view_compatible(result.type, binding.type) if container_view(result.type)
                 else result.type in binding.type.type_args or result.type == binding.type)
                and (result.readonly or not binding.readonly))
    source = binding.lent_object(summary_path(tuple(hop.projection for hop in walked.hops)))
    return not (source is None or source.type != result.type or source.readonly and not result.readonly)


def binds_at(records: Mapping[NominalType, 'MIRRecordLayout'], storage: NominalType, slot: NominalType) -> bool:
    """Whether record storage of type `storage` binds at a borrowed record
    slot of type `slot`: the type itself or one of its struct-base ancestors,
    whose fields the storage contains at the same identities. `records`
    holds the layout of `storage` whenever the two differ."""
    return slot == storage or storage in records and slot in records[storage].ancestors


def return_origin_problem(origin: MIRReturnOrigin, result: THIRBorrowedRecord,
                          parameters: tuple[MIRParameterBinding, ...], layouts: MIRRecords,
                          walks: MIRPathWalks | None = None) -> str | None:
    """Check one published return origin against the borrowed result it
    reaches (bound at the definition's receiver): a path of the grammar
    (`resolve_path`) whose endpoint the result may reach
    (`endpoint_admitted`)."""
    resolved = (resolve_path(origin.parameter, origin.path, parameters, layouts, walks)
                if isinstance(origin, MIRReturnOrigin) else None)
    if not isinstance(resolved, MIRWalk):
        return resolved or "invalid return parameter"
    if not endpoint_admitted(parameters[origin.parameter], resolved, result):
        return "unsupported return origin type or access"
    return None


def write_problem(write: MIRParameterWrite, parameters: tuple[MIRParameterBinding, ...],
                  layouts: MIRRecords, walks: MIRPathWalks | None = None) -> str | None:
    """Check one published parameter write: a non-empty path of the grammar
    (`resolve_path`) whose endpoint may be written (`endpoint_admitted`). A
    readonly parameter is never written."""
    resolved = (resolve_path(write.parameter, write.path, parameters, layouts, walks)
                if isinstance(write, MIRParameterWrite) and write.path else None)
    if not isinstance(resolved, MIRWalk):
        return resolved or "invalid call write path"
    if not endpoint_admitted(parameters[write.parameter], resolved):
        return "unsupported call write field or access"
    return None


def _stored_member(walked: MIRWalk | None) -> TpyType | None:
    """The view member a walk ends at, when a store may rebind it
    (`store_problem`), or None. A view member is the loan a record holds:
    a store rebinds that loan, never writes into what it views."""
    last = walked.hops[-1] if walked is not None and walked.hops else None
    if (last is None or not isinstance(last.projection, MIRField) or last.reached.cls is not MIRClass.VIEW
            or store_problem(walked) is not None):
        return None
    return last.reached.type


def _end_walk(end: object, path: object, parameters: tuple[MIRParameterBinding, ...], result: TpyType,
              layouts: MIRRecords, walks: MIRPathWalks | None, *, lent_only: bool = False) -> MIRWalk | str | None:
    """The walk of one end of a transfer: under a lent record parameter
    from the record it lends, any walk the summary path grammar takes
    (`_path_walk`) ending at no region;
    under owned aggregate storage -- what an owned result hands
    over, an `Own[...]` parameter's object, unless `lent_only` -- from that
    storage. The walk's refusal when a hop is refused; None when the end
    is malformed."""
    if isinstance(end, MIRResultHolder):
        owned = owned_aggregate_result(result)
        storage = None if owned is None else value_shape(owned)
    elif type(end) is int and 0 <= end < len(parameters):
        binding = parameters[end]
        if binding.lent_record is not None or binding.lent_members:
            storage = binding.value
        else:
            storage = binding.value if binding.cls in AGGREGATE_CLASSES and binding.storage and not lent_only else None
    else:
        return None
    if storage is None or not path:
        return None
    walked = _path_walk(end, path, storage, layouts, walks)
    if isinstance(walked, MIRWalk) and walked.hops and isinstance(walked.hops[-1].projection, REGION_STEPS):
        return None
    return walked


def crossed_records(summary: 'MIRCallSummary', walks: MIRPathWalks,
                    storage_of: Callable[[int], NominalType | None],
                    skip: Callable[[int, NominalType | None], bool] | None = None) -> Iterator[NominalType]:
    """The records whose fields a call's published paths (`walks`, every
    path with a field the contract check walked: `_path_walk`) read: for
    a lent parameter's first field the record its argument binds
    (`storage_of`; a descendant's layout carries its ancestors' fields),
    else the holder the walk reached it through. The caller's body carries
    each one's layout: the lowering registers it, the validator checks it
    is there. `skip` passes over a parameter path whose binding another
    check refuses."""
    for (end, _path), walked in walks.items():
        lent = type(end) is int and summary.parameters[end].lent_record is not None
        storage = storage_of(end) if lent else None
        if lent and skip is not None and skip(end, storage):
            continue
        fields = [hop for hop in walked.hops if isinstance(hop.projection, MIRField)]
        for index, hop in enumerate(fields):
            yield storage if index == 0 and storage is not None else hop.holder.type


def transfer_problem(transfer: MIRLoanTransfer, parameters: tuple[MIRParameterBinding, ...],
                     result: TpyType, layouts: MIRRecords, walks: MIRPathWalks | None = None) -> str | None:
    """Check one published loan transfer: its holder path ends at a view
    member a store may rebind (`_stored_member`) under a lent record
    parameter -- what an `Own[R]` parameter's object stores, the caller
    never reads again -- or under the owned-record result `result` hands
    over; its source is a literal, a view or lent owned-leaf parameter of
    the member's family, or a view member of a record parameter (lent, or
    handed over as `Own[R]`)."""
    if not isinstance(transfer, MIRLoanTransfer):
        return "invalid loan transfer"
    walked = _end_walk(transfer.holder, transfer.path, parameters, result, layouts, walks, lent_only=True)
    if isinstance(walked, str):
        return walked
    member = _stored_member(walked)
    if member is None:
        return "invalid loan transfer holder"
    source = transfer.source
    if isinstance(source, MIRStaticLoan):
        return None if transfer.source_path == () else "invalid loan transfer source"
    if type(source) is not int or not 0 <= source < len(parameters) or not isinstance(transfer.source_path, tuple):
        return "invalid loan transfer source"
    binding = parameters[source]
    if not transfer.source_path:
        lent = not binding.protocol and binding.value.leaf_borrow
        return None if lent and view_compatible(member, binding.type) else "invalid loan transfer source"
    walked = _end_walk(source, transfer.source_path, parameters, result, layouts, walks)
    if isinstance(walked, str):
        return walked
    loan = walked.end if walked is not None else None
    if loan is None or loan.cls is not MIRClass.VIEW or not view_compatible(member, loan.type):
        return "invalid loan transfer source"
    return None


def obligation_problem(obligation: MIRAliasObligation, summary: MIRCallSummary, layouts: MIRRecords,
                       walks: MIRPathWalks | None = None) -> str | None:
    """Check one published no-alias obligation: its written place is one of
    the summary's writes, and the place it retains is a path of the grammar
    (`resolve_path`) under another parameter, or in another object one
    parameter lends (`shapes.distinct_lent_objects`)."""
    if not isinstance(obligation, MIRAliasObligation) or obligation.written not in summary.writes:
        return "invalid alias obligation write"
    resolved = resolve_path(obligation.parameter, obligation.path, summary.parameters, layouts, walks)
    if resolved is None:
        return "invalid alias obligation parameter"
    if obligation.parameter == obligation.written.parameter and not distinct_lent_objects(
            summary.parameters[obligation.parameter].value, obligation.path, obligation.written.path):
        return "invalid alias obligation parameter"
    return resolved if isinstance(resolved, str) else None


def summary_problem(summary: MIRCallSummary, layouts: MIRRecords,
                    walks: MIRPathWalks | None = None) -> str | None:
    """Validate the bounded contract without re-proving its supplying body;
    its paths walk over `layouts` (`_path_walk`), each walk recorded in
    `walks` when given (`crossed_records` reads them). A stub summary must be
    exactly the one its declaration derives, so a hand-built summary cannot
    claim more than the stub declares."""
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
                   for indices in (summary.reads, summary.invalidates))
            or not isinstance(summary.transfers, frozenset)
            or not isinstance(summary.obligations, frozenset)
            or not isinstance(summary.returns, frozenset)
            or any(not isinstance(origin, MIRReturnOrigin) for origin in summary.returns)
            or not isinstance(summary.global_reads, frozenset)
            or any(not isinstance(g, MIRGlobalId) or not (isinstance(g.module, str) and g.module
                                                          and isinstance(g.name, str) and g.name)
                   for g in summary.global_reads)):
        return "invalid call summary identity or facts"
    if (result_problem(signature_result(signature), call_result(summary.callee, None),
                       signature.return_representation) is not None
            or len(summary.parameters) != len(signature.param_types)
            or summary.reads != frozenset(range(len(summary.parameters)))
            or not isinstance(summary.writes, frozenset)
            or summary.invalidates
            or type(summary.normal_return_only) is not bool):
        return "unsupported call summary contract"
    for typ, binding in zip(signature_params(signature), summary.parameters):
        # The binding's declared type is the signature's, so a forged one does not pass.
        if not isinstance(binding, MIRParameterBinding):
            return "malformed call parameter binding"
        if binding.declared != typ:
            return "call parameter binding declares another type"
        problem = parameter_contract_problem(binding)
        if problem is not None:
            return problem
    # A method's callee binds its receiver first: a borrowed record of the owner.
    receiver = summary.parameters[0].lent_record if summary.parameters else None
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
        problem = return_origin_problem(origin, result, summary.parameters, layouts, walks)
        if problem is not None:
            return problem
    for write in summary.writes:
        problem = write_problem(write, summary.parameters, layouts, walks)
        if problem is not None:
            return problem
    for transfer in summary.transfers:
        problem = transfer_problem(transfer, summary.parameters, signature_result(signature), layouts, walks)
        if problem is not None:
            return problem
    for obligation in summary.obligations:
        problem = obligation_problem(obligation, summary, layouts, walks)
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
