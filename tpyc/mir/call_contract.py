"""Immutable certificates for the bounded call interface."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum, auto

from ..thir.nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRFieldIdentity, THIRFunction, THIRFunctionIdentity,
    THIRNativeContainer, THIRParam, THIRResolvedCallee, THIRStubCallee, THIRStubContract, THIRStubIdentity,
    declared_param_type, effective_params,
)
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, container_element, container_view, holds_loan, modeled_members, native_container_subject,
    native_container_type, optional_record_field, owned_leaf, owned_value_type, plain_record_element,
    record_type, storage_leaf, value_class, view_compatible, view_endpoint, view_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import (
    NominalType, OwnType, ReadonlyType, RefType, Representation, TpyType, VoidType, is_owned_leaf,
    is_protocol_type, passing_representation, return_representation, unwrap_own, unwrap_readonly, unwrap_ref_type,
)
from .value import (
    CONTAINER_CLASSES, WRAPPER_CLASSES, Declared, MIRHolding, MIRRecordLayout, MIRRole, MIRShape, Passing,
    container_members, fact_access_problem, fact_shape, handed_type, holding_of, laid_value, layout_fact, lent_record,
    loan_access, shape_problem, thir_member, value_shape,
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
class MIRProtocolParameter:
    """A stub's protocol parameter: no value MIR models until an argument
    binds it (the argument's fact, checked at each call); read only."""
    readonly: bool


class MIRBindingContext(Enum):
    """Who binds a parameter without declaring its access: a function body
    or summary, or a constructor's definition (what it is handed it moves
    into a member, private to no one)."""
    FUNCTION = auto()
    CONSTRUCTOR = auto()


# The one context input of a binding's derivation: a function's, a
# constructor's, or a stub's (its declared access, which is also the
# loan's access source).
BindingContext = MIRBindingContext | Declared
FUNCTION = MIRBindingContext.FUNCTION
CONSTRUCTOR = MIRBindingContext.CONSTRUCTOR


@dataclass(frozen=True)
class MIRParameterBinding:
    """How a callee binds one parameter (`derived_binding` derives it,
    `parameter_contract_problem` checks it): its declared type, with the
    `Own[...]` / `readonly[...]` wrapper its value's type drops; the value
    the callee holds (`value_shape`), or a protocol parameter's; the
    passing THIR published; whether the object is handed over for the
    callee alone (`private_owned`, decided by the constructor context)."""
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
        return None if self.protocol else lent_record(self.value)

    @property
    def readonly(self) -> bool:
        """Whether the caller's storage is lent readonly: the lent record's
        access, else the value's; False for the callee's own storage."""
        if self.storage:
            return False
        record = self.lent_record
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
    bare = unwrap_readonly(unwrap_ref_type(typ))
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
    if is_protocol_type(unwrap_readonly(unwrap_ref_type(typ))):
        return True, None
    return False, value_class(native_container_subject(typ))


def parameter_binding(p: THIRParam, context: BindingContext = FUNCTION) -> MIRParameterBinding | str:
    """How a callee binds parameter `p` in `context` -- a body's parameter
    or receiver, a summary's, a constructor's, a stub's -- by the one
    derivation (`derived_binding`) from the value THIR's one fact lays out
    (`layout_fact`, `laid_value`; a container's fact read at its type,
    `container_fact_problem`), checked by the contract
    (`parameter_contract_problem`), the fact's access checked, never read
    (`fact_access_problem`). Else the reason; what a stub takes is its
    caller's to check (`_stub_binding`)."""
    passing = p.passing
    if not isinstance(passing, ParamPassing):
        return "unpublished parameter passing"
    _, cls = _parameter_class(p.type)
    container = p.native_container
    fact = layout_fact(p, others=("native_container",))
    laid = fact if fact is None or isinstance(fact, str) else laid_value(p.type, fact)
    if isinstance(laid, str):
        return laid
    if container is not None and (problem := container_fact_problem(container, p.type)) is not None:
        return problem
    if container is not None and cls not in CONTAINER_CLASSES:
        return "conflicting layout facts"
    binding = derived_binding(p.type, passing, laid, context)
    if isinstance(binding, str):
        return binding
    stated = container if container is not None else laid
    return ((not binding.protocol
             and shape_problem(binding.value, p.type, representation=passing_representation(passing)))
            or fact_access_problem(binding.cls, None if stated is None else stated.readonly, binding.readonly)
            or binding)


def bound_params(fn: THIRFunction) -> tuple[THIRParam, ...] | None:
    """The parameters callers of `fn` bind (`effective_params`: a method's
    receiver first), at the types its signature declares (an explicit
    `@readonly` body reads `readonly[P]`, callers bind `Ref[P]`); None if
    it declares no others."""
    params = effective_params(fn)
    types = fn.resolved_callee.signature.param_types if fn.resolved_callee is not None else None
    if types is None or tuple(map(declared_param_type, types)) != tuple(declared_param_type(p.type) for p in params):
        return None
    return tuple(replace(p, type=t) for p, t in zip(params, types))


def derived_binding(declared: TpyType, passing: ParamPassing, laid: MIRShape | None,
                    context: BindingContext = FUNCTION) -> MIRParameterBinding | str:
    """The one derivation of a binding from its declared type, passing,
    context and the value a fact lays out (`laid`: a lent record, a
    wrapper -- THIR's, or the one a binding holds; of the declared type's
    class); else the reason. The class is the subject type's (a protocol
    parameter has none); passed by value the callee's own storage
    (`parameter_storage`), private to it when handed over outside a
    constructor; else the caller's loan at the access its source gives
    (`loan_access`: a stub's declaration, else its passing), an Optional
    record's its payload's. A fact lays out only what the binding lends or
    wraps (`takes_layout`). The passing is its type's (`_passing_problem`,
    checked first: it decides the refusal), the holding one the class
    takes (`_holding_problem`).
    Outside a stub the binding is a function's or a constructor's: no
    protocol parameter, no scalar element handed over, a record handed
    over only as exactly `Own[R]` at OWN (`R&&`; a value-type record is
    passed by value). What the value is checked against is the contract's
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
    lent = cls in (MIRClass.RECORD, MIRClass.CONTAINER) and not storage
    if (problem := _passing_problem(declared, passing, readonly if lent else None)) is not None:
        return problem
    # A fact lays out exactly what the binding lends or wraps.
    if (laid is not None) is not takes_layout(cls, storage):
        return "conflicting layout facts" if laid is not None else "unsupported parameter type"
    if (problem := _holding_problem(cls, declared, passing, storage, handed, laid)) is not None:
        return problem
    subject = native_container_subject(declared)
    if protocol:
        value: MIRShape | MIRProtocolParameter | str = MIRProtocolParameter(readonly)
    elif laid is not None and cls is MIRClass.RECORD:
        # The record the binding lends, at the binding's access.
        value = value_shape(laid.type, cls, access=readonly, role=MIRRole.LOAN)
    elif laid is not None:
        value = laid
    elif cls in CONTAINER_CLASSES and isinstance(members := container_members(subject, readonly), str):
        return members
    else:
        value = value_shape(subject, cls, access=readonly, role=MIRRole.STORAGE if storage else MIRRole.LOAN)
    if not stub and (protocol or handed and (cls is MIRClass.LEAF or cls is MIRClass.RECORD and not (
            passing is ParamPassing.OWN and isinstance(declared, OwnType) and declared.wrapped == value.type))):
        return "unsupported parameter type"
    return MIRParameterBinding(declared, value, passing, handed and context is not CONSTRUCTOR)


def function_binding(p: THIRParam, context: MIRBindingContext = FUNCTION) -> MIRParameterBinding | str:
    """How a function body or a constructor's definition binds `p`: its one
    binding, of THIR's facts."""
    binding = parameter_binding(p, context)
    return binding if isinstance(binding, str) else published_problem(p) or binding


def published_problem(p: THIRParam) -> str | None:
    """What a body's parameter `p` needs of THIR's facts beyond their form
    (`parameter_binding`; a stub publishes none): a container's present, of
    the element its type derives (`native_container_problem`); a container
    view's present."""
    _, cls = _parameter_class(p.type)
    subject = native_container_subject(p.type)
    if cls is MIRClass.CONTAINER:
        return native_container_problem(p.native_container, subject)
    if cls is MIRClass.CONTAINER_VIEW and p.native_container is None:
        # As for a container: members MIR does not model are refused as such.
        members = container_members(subject, False)
        return members if isinstance(members, str) else "missing native container fact"
    return None


def container_read_problem(container: TpyType, readonly: object, typ: TpyType) -> str | None:
    """A container (a fact, or a borrowed container binding) read at `typ`:
    of exactly its container, never with more access than `typ` grants."""
    bare = native_container_subject(typ)
    if not (isinstance(bare, NominalType) and container == bare and type(readonly) is bool
            and (not isinstance(unwrap_ref_type(typ), ReadonlyType) or readonly)):
        return "invalid native container fact"
    return None


def container_fact_problem(fact: object, typ: TpyType) -> str | None:
    """A container fact (a container's or a container view's) for a value
    held at `typ`: one, read at `typ` (`container_read_problem`)."""
    if not isinstance(fact, THIRNativeContainer):
        return "invalid native container fact"
    return container_read_problem(fact.type, fact.readonly, typ)


def native_container_problem(fact: THIRNativeContainer | None, typ: TpyType) -> str | None:
    """THIR's fact for a native container held at `typ`, of a checked form
    (`container_fact_problem`): present (THIR declines one for members MIR
    does not model -- their refusal -- or cannot state, as
    `list[readonly[int32]]`), its element the member the type derives
    (`container_members`; a record element at the container's access)."""
    bare = native_container_subject(typ)
    if fact is not None and not native_container_type(bare):
        return "unsupported native container type"
    members = container_members(bare, False)
    if isinstance(members, str):
        return members
    if fact is None:
        return "missing native container fact"
    # THIR states a record element as a pointer at the container's access, any other by its type.
    element, expected = thir_member(fact.element), members[0]
    record = element.cls is MIRClass.RECORD
    if not (element.type == expected.type and record is (expected.cls is MIRClass.RECORD)
            and (not record or element.is_pointer and element.readonly is fact.readonly)):
        return "unsupported native container element"
    return None


def _holding_problem(cls: MIRClass | None, typ: TpyType, passing: ParamPassing, storage: bool, handed: bool,
                     laid: MIRShape | None) -> str | None:
    """How each class is held beyond its type's passing (`_passing_problem`),
    `laid` its laid-out value: a scalar leaf by value, or handed over (to a
    container); a record or container lent, or handed over; an Optional at
    its payload's passing (`P | None` a pointer at the caller's record, a
    scalar Optional by value); a leaf view or a region view (`region_view`),
    never declared handed over."""
    match cls:
        case MIRClass.LEAF:
            if not (handed or storage and passing is ParamPassing.VALUE and typ == native_container_subject(typ)):
                return "unsupported parameter type"
        case MIRClass.RECORD | MIRClass.CONTAINER:
            if not handed and storage:
                return "unsupported reference parameter passing"
        case MIRClass.OPTIONAL:
            if passing is not (ParamPassing.POINTER if lent_record(laid) is not None else ParamPassing.VALUE):
                return "unsupported optional parameter passing"
        case MIRClass.VIEW | MIRClass.CONTAINER_VIEW:
            if handed_type(typ):
                return "unsupported parameter type"
            if cls is MIRClass.CONTAINER_VIEW and not region_view(typ):
                return "view of container storage"
    return None


def takes_layout(cls: MIRClass | None, storage: bool) -> bool:
    """Whether a binding of class `cls` holds a value a fact lays out: a
    wrapper, or a record it lends (one handed over is its own storage)."""
    return cls in WRAPPER_CLASSES or cls is MIRClass.RECORD and not storage


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
    laid = value if takes_layout(binding.cls, binding.storage) else None
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
                lends: bool = False, declared: TpyType | None = None) -> THIRBorrowedRecord | None:
    """What one call of `callee` returns borrowed, its receiver at access
    `receiver_readonly` -- or, None, the access the signature publishes,
    no receiver adjusting it (the body's `MIRFunction.borrowed_result` and
    its callers' summaries) -- the one result path, the callee's kind an input.
    A body THIR resolved no callee for (an overload) is a user callee
    declaring `declared`, with no borrowed fact and no receiver it follows:
    a user callee's borrowed record (THIR's fact); a view (`view_result`);
    a method stub's record returned by reference (`method_record_result`);
    a user callee's container returned by reference (`container_result`);
    a readonly borrow of a stub's owned-leaf result when the stub lends it
    arguments (`lends`). A result borrowing the receiver takes the
    receiver's access too: a method stub's view or record result, whatever
    access the stub binds the receiver at (an `@auto_readonly` pure clone
    binds it mutably), and a user callee's result following its receiver
    (`@auto_readonly`: C++ picks the clone by the receiver's constness).
    None for a fresh result."""
    signature = callee.signature if callee is not None else None
    typ = signature.return_type if signature is not None else declared
    stub = isinstance(callee, THIRStubCallee)
    follows = signature is not None and receiver_readonly is not None and (
        callee.receiver if stub else signature.result_follows_receiver)
    receiver = bool(follows and receiver_readonly)
    if not stub and signature is not None and (record := signature.borrowed_result) is not None:
        if not follows or view_leaf(record.type):
            return record
        return THIRBorrowedRecord(record.type, loan_access(typ, value_class(record.type), Declared(receiver)))
    if (view := view_result(typ, receiver)) is not None:
        return view
    if not stub:
        return container_result(typ, receiver)
    if (record := method_record_result(callee, receiver)) is not None:
        return record
    owned = owned_value_type(typ)
    if owned is None or not lends:
        return None
    return THIRBorrowedRecord(owned, loan_access(owned, MIRClass.OWNED_LEAF, None))


def view_result(typ: TpyType, receiver_readonly: bool = False) -> THIRBorrowedRecord | None:
    """The holder a view-returning callee hands its caller: a borrow typed
    by the view, returned as a VIEW -- a view leaf (`view_leaf`, readonly)
    or a container view whose members MIR models (`modeled_members`;
    readonly when its element argument is, or when it views a receiver
    bound readonly, `receiver_readonly`). None for any other result."""
    if return_representation(typ) is not Representation.VIEW:
        return None
    if view_leaf(typ):
        return THIRBorrowedRecord(typ, loan_access(typ, MIRClass.VIEW, None))
    if container_view(typ) and modeled_members(typ):
        return THIRBorrowedRecord(typ, loan_access(typ, MIRClass.CONTAINER_VIEW, Declared(receiver_readonly)))
    return None


def method_record_result(callee: THIRStubCallee, place_readonly: bool = False) -> THIRBorrowedRecord | None:
    """The element a method stub returns by reference (`setdefault` on a
    dict of records): a borrow of its receiver's storage, at the access the
    receiver is bound at -- its declared one, readonly too at a readonly
    receiver place (`place_readonly`). None for any other result."""
    typ = callee.signature.return_type
    if not callee.receiver or return_representation(typ) is not Representation.REFERENCE:
        return None
    bare = unwrap_readonly(unwrap_ref_type(typ))
    if not plain_record_element(bare):
        return None
    return THIRBorrowedRecord(bare, loan_access(typ, MIRClass.RECORD, Declared(callee.readonly[0] or place_readonly)))


def container_result(typ: TpyType, receiver_readonly: bool = False) -> THIRBorrowedRecord | None:
    """The borrow a native container returned by reference hands its
    caller, readonly when its type is or it borrows a receiver bound
    readonly (`receiver_readonly`); None for any other result (an
    `Own[...]` container returns storage)."""
    bare = unwrap_readonly(unwrap_ref_type(typ))
    if return_representation(typ) is not Representation.REFERENCE or not native_container_type(bare):
        return None
    return THIRBorrowedRecord(bare, loan_access(typ, MIRClass.CONTAINER, Declared(receiver_readonly)))


def _passing_problem(typ: TpyType, passing: ParamPassing, loan_readonly: bool | None = None) -> str | None:
    """A passing is the type's own at one of the two const verdicts; a
    loan of a record or container's at its access (`loan_readonly`: a
    stub's declaration, not its passing, decides a stub loan's)."""
    verdicts = (False, True) if loan_readonly is None else (loan_readonly,)
    if passing not in {typ.param_passing(readonly) for readonly in verdicts}:
        return "call parameter passing differs from its type"
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
        # follows-receiver result bound at a readonly receiver): the region
        # it lays out, checked once (`shape_problem`). A container whose
        # members MIR does not model has no region to build.
        if (container_result(typ) is None or type(ref.readonly) is not bool
                or isinstance(container_members(ref.type, ref.readonly), str)):
            return "invalid borrowed result"
        region = value_shape(ref.type, access=ref.readonly, role=MIRRole.LOAN)
        return None if shape_problem(region, typ) is None else "invalid borrowed result"
    if (not isinstance(ref, THIRBorrowedRecord) or isinstance(fact_shape(typ, ref), str)
            or not isinstance(typ, (RefType, ReadonlyType))):
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
        binding = _stub_binding(callee, typ, passing, readonly, method=False)
        if isinstance(binding, str):
            return binding
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
    receiver_binding = parameter_binding(THIRParam("self", receiver, passing=signature.passings[0]),
                                         Declared(callee.readonly[0]))
    if isinstance(receiver_binding, str):
        return receiver_binding
    bindings = [receiver_binding]
    for typ, passing, readonly in zip(signature.param_types[1:], signature.passings[1:], callee.readonly[1:]):
        binding = _stub_binding(callee, typ, passing, readonly, method=True)
        if isinstance(binding, str):
            return binding
        bindings.append(binding)
    writes: frozenset[MIRParameterWrite] = frozenset()
    if callee.contract is not THIRStubContract.PURE and not callee.readonly[0]:
        projection = (MIRContainerElements() if callee.mutates_elements or view else MIRContainerStructure())
        writes = frozenset({MIRParameterWrite(0, (projection,))})
    result = signature.return_type
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
    ref = binding.lent_record if isinstance(binding, MIRParameterBinding) else None
    if ref is None:
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
    readonly = binding.lent_record.readonly or any(isinstance(f.type, ReadonlyType) for _, f in hops)
    return MIRResolvedPath(hops, projection, readonly)


def record_field(field: THIRFieldIdentity, ref: MIRShape | None) -> bool:
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
    leaf (its buffer replaced), a record member or an Optional record field
    (replaced whole; a write under its payload is published as one) or a
    container projection of a container field whose members MIR models. A
    return ends at no projection: a view result views an owned-leaf field
    of its family or is the loan a view member of its family stores; a
    container or record result is a field of exactly its type, never lent
    with more access than the path lends.

    The whole parameter (no field): a write projects a mutable borrowed or
    owned container, or the elements of a writable container view (a view
    cannot change its container's shape). A return is a lent leaf a view
    result views, a container parameter whose elements or whole self the
    result is, or a record parameter the record result is."""
    if not resolved.hops:
        return _whole_parameter_endpoint(binding, resolved.projection, result)
    if not all(record_field(field, binding.lent_record) for _, field in resolved.hops):
        return False
    bare = unwrap_readonly(resolved.hops[-1][1].type)
    if result is None:
        if resolved.readonly:
            return False
        if resolved.projection is not None:
            return native_container_type(bare) and modeled_members(bare)
        return storage_leaf(bare) or owned_leaf(bare) or record_type(bare) or optional_record_field(bare) is not None
    if resolved.projection is not None:
        return False
    if view_leaf(result.type):
        return view_endpoint(result.type, bare)
    return ((native_container_type(bare) and modeled_members(bare) or record_type(bare))
            and bare == result.type and (result.readonly or not resolved.readonly))


def _whole_parameter_endpoint(binding: MIRParameterBinding,
                              projection: MIRContainerStructure | MIRContainerElements | None,
                              result: THIRBorrowedRecord | None) -> bool:
    container = binding.cls in (MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW)
    if result is None:
        if projection is None or binding.readonly or not container or not modeled_members(binding.type):
            return False
        return binding.cls is MIRClass.CONTAINER or isinstance(projection, MIRContainerElements)
    if projection is not None:
        return False
    if view_leaf(result.type):
        # A view result borrows what a lent parameter of its family reaches.
        return not binding.protocol and binding.value.leaf_borrow and view_compatible(result.type, binding.type)
    if container:
        # A container view or element result rooted in a container
        # parameter's elements is published as the whole parameter, as
        # is the container itself.
        return ((view_compatible(result.type, binding.type) if container_view(result.type)
                 else result.type in binding.type.type_args or result.type == binding.type)
                and (result.readonly or not binding.readonly))
    source = binding.lent_record
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


def member_path(root: TpyType, path: object) -> tuple[MIRPathHop, ...] | None:
    """The hops of a path of fields under record storage of type `root`
    (an owned-record result, an `Own[R]` parameter), as `path_hops`
    walks one under a borrowed record; None when it is malformed. Each
    field's membership in its hop's layout is checked where layouts are
    known (the call, the summary)."""
    if not isinstance(path, tuple) or not path or not record_type(root):
        return None
    hops: list[MIRPathHop] = []
    current = root
    for field in path:
        if not (isinstance(field, THIRFieldIdentity) and record_type(current) and record_type(field.owner)
                and isinstance(field.name, str) and bool(field.name)):
            return None
        hops.append((current, field))
        current = unwrap_readonly(field.type)
    return tuple(hops)


def _stored_member(hops: tuple[MIRPathHop, ...] | None) -> TpyType | None:
    """The view member a path of hops ends at, or None."""
    if not hops:
        return None
    last = hops[-1][1].type
    return last if view_leaf(last) and not isinstance(last, ReadonlyType) else None


@dataclass(frozen=True)
class MIRTransferEnd:
    """One end of a loan transfer that names a path. Under a lent record
    parameter (`parameter`) the path's hops start at the record the argument
    binds (`path_hops`), which only a call or a summary knows; under owned
    record storage -- the record an owned-record result hands over, an
    `Own[R]` parameter's object -- they are walked from its type
    (`member_path`) into `owned_hops`, None when malformed."""
    parameter: int | None
    path: MIRParameterPath
    owned_hops: tuple[MIRPathHop, ...] | None = None


def transfer_end(end: object, path: object, parameters: tuple[MIRParameterBinding, ...],
                 result: TpyType) -> MIRTransferEnd | None:
    """Where one end of a transfer reads its path (`MIRTransferEnd`); None
    for an end that is neither the result nor a parameter."""
    if isinstance(end, MIRResultHolder):
        owned = owned_record_result(result)
        return MIRTransferEnd(None, path, member_path(owned, path) if owned is not None else None)
    if type(end) is not int or not 0 <= end < len(parameters):
        return None
    binding = parameters[end]
    if binding.lent_record is not None:
        return MIRTransferEnd(end, path)
    handed = binding.cls is MIRClass.RECORD and binding.storage
    return MIRTransferEnd(None, path, member_path(binding.type, path) if handed else None)


def transfer_ends(transfer: MIRLoanTransfer, parameters: tuple[MIRParameterBinding, ...],
                  result: TpyType) -> tuple[MIRTransferEnd | None, ...]:
    """The holder's end of a transfer and, when the source is a view member
    of a parameter's object, the source's (`transfer_end`)."""
    ends = [transfer_end(transfer.holder, transfer.path, parameters, result)]
    if not isinstance(transfer.source, MIRStaticLoan) and transfer.source_path:
        ends.append(transfer_end(transfer.source, transfer.source_path, parameters, result))
    return tuple(ends)



def _has_field(layout: 'MIRRecordLayout | None', field: THIRFieldIdentity) -> bool:
    """Whether a layout carries the field (`MIRField` membership, keyed by its declaring owner)."""
    return layout is not None and any(f.id.owner == field.owner and f.id.name == field.name and f.type == field.type
                                      for f in layout.fields)


def call_path_problem(summary: 'MIRCallSummary', return_type: TpyType,
                      storage_of: Callable[[int], NominalType | None],
                      layout_of: Callable[[NominalType], 'MIRRecordLayout | None'],
                      skip: Callable[[int, NominalType | None], bool] | None = None) -> str | None:
    """The problem of a call's paths against the record layouts it binds,
    or None: each field of a transfer under owned record storage, then of
    every write, return and lent-transfer path, is a member of the record
    storage it is read from -- hop 0 the record the argument binds
    (`storage_of` its parameter; a descendant's layout carries its
    ancestors' fields), hop k the previous field's member record. The
    caller's `layout_of` may register what it returns, so it is called in
    this order and only as far as the walk gets; `skip` passes over a
    parameter path whose binding another check refuses."""
    transferred: list[tuple[int, object]] = []
    for transfer in summary.transfers:
        for end in transfer_ends(transfer, summary.parameters, return_type):
            if end is None:
                return "invalid loan transfer"
            if end.parameter is not None:
                transferred.append((end.parameter, end.path))
            elif end.owned_hops is None or not all(_has_field(layout_of(record), field)
                                                   for record, field in end.owned_hops):
                return "call write field does not match record layout"
    for parameter, path in (*((w.parameter, w.path) for w in summary.writes),
                            *((o.parameter, o.path) for o in summary.returns), *transferred):
        storage = storage_of(parameter)
        if skip is not None and skip(parameter, storage):
            continue
        hops = path_hops(summary.parameters[parameter], path, storage)
        if hops is None or not all(_has_field(layout_of(record), field) for record, field in hops):
            return "call write field does not match record layout"
    return None


def transfer_problem(transfer: MIRLoanTransfer, parameters: tuple[MIRParameterBinding, ...],
                     result: TpyType) -> str | None:
    """Check one published loan transfer: its holder path ends at a view
    member under a record parameter lent mutably, or under the owned-record
    result `result` hands over; its source is a literal, a view or lent
    owned-leaf parameter of the member's family, or a view member of a
    record parameter (lent, or handed over as `Own[R]`)."""
    if not isinstance(transfer, MIRLoanTransfer):
        return "invalid loan transfer"
    holder = transfer.holder
    end = transfer_end(holder, transfer.path, parameters, result)
    if end is None or (end.parameter is None) is not isinstance(holder, MIRResultHolder):
        # A parameter holds a transfer only when lent: what an `Own[R]`
        # parameter's object stores, the caller never reads again.
        hops = None
    elif end.parameter is None:
        hops = end.owned_hops
    else:
        resolved = resolve_path(holder, transfer.path, parameters)
        hops = (resolved.hops if resolved is not None and resolved.projection is None and not resolved.readonly
                else None)
    member = _stored_member(hops)
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
    end = transfer_end(source, transfer.source_path, parameters, result)
    if end.parameter is None:
        hops = end.owned_hops
    else:
        resolved = resolve_path(source, transfer.source_path, parameters)
        hops = resolved.hops if resolved is not None and resolved.projection is None else None
    loan = unwrap_readonly(hops[-1][1].type) if hops else None
    return None if view_leaf(loan) and view_compatible(member, loan) else "invalid loan transfer source"


def obligation_problem(obligation: MIRAliasObligation, summary: MIRCallSummary) -> str | None:
    """Check one published no-alias obligation: its written place is one of
    the summary's writes, and the place it retains is a path of the grammar
    (`resolve_path`) under another parameter."""
    if not isinstance(obligation, MIRAliasObligation) or obligation.written not in summary.writes:
        return "invalid alias obligation write"
    if (obligation.parameter == obligation.written.parameter
            or resolve_path(obligation.parameter, obligation.path, summary.parameters) is None):
        return "invalid alias obligation parameter"
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
    if (result_problem(signature.return_type, call_result(summary.callee, None)) is not None
            or len(summary.parameters) != len(signature.param_types)
            or summary.reads != frozenset(range(len(summary.parameters)))
            or not isinstance(summary.writes, frozenset)
            or summary.invalidates
            or type(summary.normal_return_only) is not bool):
        return "unsupported call summary contract"
    for typ, binding in zip(signature.param_types, summary.parameters):
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
        problem = return_origin_problem(origin, result, summary.parameters)
        if problem is not None:
            return problem
    for write in summary.writes:
        problem = write_problem(write, summary.parameters)
        if problem is not None:
            return problem
    for transfer in summary.transfers:
        problem = transfer_problem(transfer, summary.parameters, signature.return_type)
        if problem is not None:
            return problem
    for obligation in summary.obligations:
        problem = obligation_problem(obligation, summary)
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
