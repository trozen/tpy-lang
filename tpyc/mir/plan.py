"""The lowering plan: what MIR's check pass decided for a body, one node per
lowering operation. The emit pass (`emit.py`) builds the CFG from these
decisions and never reads the THIR they were made from.

A plan carries source locations and the shapes of the slots the emit pass
materializes (where the shape depends on the type's facts). The THIR objects
it holds the emit pass never reads: the keys of THIR's own tables a backing
or a borrow operation is published under (`key`, `operation`), the
declaration a refusal names (`missing_placement`), the THIR facts the check
pass carries between its own methods (`PTupleName.layout` and
`PTupleLiteral.layout`, a `THIRTupleLayout`; `PRecordSource.record`, a
`THIRBorrowedRecord`), and the body's borrowed result (`PBody.result`),
which the MIR function keeps as it is."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..parse import SourceLocation
from ..thir.nodes import THIRBorrowedRecord, THIRTupleLayout
from ..typesys import TpyType
from .call_contract import MIRCallSummary
from .definitions import MIRFieldInitializer
from .nodes import MIRBodyKind, MIRField, MIRGlobalId, MIRRecordLayout, MIRRvalue, MIRShape, MIRStorageDuration

Loc = SourceLocation | None


@dataclass(frozen=True)
class PBinding:
    """A named binding: the shape of the slot holding it and, for a holder of
    record storage the binding owns (an owned record local or parameter, an
    Optional record backing), the shape of that storage."""
    name: str
    shape: MIRShape
    backing: MIRShape | None = None


@dataclass(frozen=True)
class PCall:
    """A call through its summary, one argument plan per parameter (a
    method's receiver is parameter 0, `method`)."""
    loc: Loc
    summary: MIRCallSummary
    arguments: tuple[object, ...]
    method: bool


# --- places -------------------------------------------------------------------

@dataclass(frozen=True)
class PGlobalPlace:
    identity: MIRGlobalId


@dataclass(frozen=True)
class PNamePlace:
    """A binding's slot; `payload`: a narrowed Optional's payload in it."""
    name: str
    payload: bool = False


@dataclass(frozen=True)
class PUnionPayloadPlace:
    """The payload of a narrowed union binding's alternative."""
    name: str
    alternative: int


@dataclass(frozen=True)
class PContainerField:
    loc: Loc
    place: 'PFieldPlace'


@dataclass(frozen=True)
class PContainerCall:
    """The container a user call returns by reference, or the container
    view a method stub returns over its receiver's region."""
    loc: Loc
    call: PCall


@dataclass(frozen=True)
class PContainer:
    """A container place: its type, its members under the place's access,
    that access, how it is reached (a binding: owned, borrowed or a view's
    region), and the shape of a holder of it (None for a binding, which is
    its own holder)."""
    type: TpyType
    members: tuple[MIRShape, ...]
    readonly: bool
    root: PNamePlace | PContainerField | PContainerCall
    holder: MIRShape | None = None

    @property
    def element(self) -> MIRShape:
        """What iterating the container yields: a list / set / Array element, a dict key."""
        return self.members[0]

    @property
    def keyed(self) -> bool:
        """A dict: a key addresses the value member."""
        return len(self.members) > 1

    @property
    def subscript(self) -> MIRShape:
        """The member a subscript yields: a dict's value, else the element."""
        return self.members[-1]


@dataclass(frozen=True)
class PElementPlace:
    """A container's element region, after its index (or a key, which the
    lookup reads through a holder: `key`) is evaluated; `raises`: the index
    is not proven in range, or a key may be missing."""
    index: object
    key: bool
    container: PContainer
    raises: bool

    @property
    def member(self) -> MIRShape:
        """The member the subscript reaches."""
        return self.container.subscript


@dataclass(frozen=True)
class PTupleElementPlace:
    """A tuple member: of a binding (`root` a name) or of a tuple value."""
    root: 'str | PTupleName | PTupleLiteral'
    index: int


@dataclass(frozen=True)
class PFieldPlace:
    """A record field under its receiver's storage; `payload`: a narrowed
    Optional field's inline payload."""
    receiver: object
    field: MIRField
    payload: bool = False


@dataclass(frozen=True)
class PTemporaryRecordPlace:
    """A record built into storage of its full expression, read in place;
    `key` is the expression its backing is published under."""
    key: object
    loc: Loc
    storage: MIRShape
    value: object


# --- scalar values ------------------------------------------------------------

@dataclass(frozen=True)
class PConstant:
    loc: Loc
    type: TpyType
    value: object


@dataclass(frozen=True)
class PRead:
    loc: Loc
    type: TpyType
    place: object


@dataclass(frozen=True)
class PCallValue:
    loc: Loc
    type: TpyType
    call: PCall


@dataclass(frozen=True)
class PConvert:
    """A conversion of an operand; `owned`: the result is an owned leaf,
    built into storage where an operation reads it."""
    loc: Loc
    type: TpyType
    name: str
    operand: object
    owned: bool = False


@dataclass(frozen=True)
class PPromote:
    """An operator operand converted into the promotion's type first; an
    owned result lives in storage of the full expression."""
    loc: Loc
    type: TpyType
    name: str
    operand: object
    owned: bool


@dataclass(frozen=True)
class POperation:
    """A primitive operation; `owned`: its result is an owned leaf, built
    into storage where another operation reads it."""
    loc: Loc
    type: TpyType
    op: str
    operands: tuple[object, ...]
    owned: bool = False


@dataclass(frozen=True)
class PCompare:
    loc: Loc
    type: TpyType
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class PNot:
    loc: Loc
    type: TpyType
    operand: object


@dataclass(frozen=True)
class PLogical:
    """`a && b` / `a || b`: the right operand evaluates only when it decides."""
    loc: Loc
    type: TpyType
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class PConditional:
    loc: Loc
    type: TpyType
    cond: object
    then: object
    orelse: object


@dataclass(frozen=True)
class PIsAlternative:
    loc: Loc
    type: TpyType
    name: str
    alternatives: tuple[int, ...]
    negate: bool


@dataclass(frozen=True)
class PIsPresent:
    loc: Loc
    type: TpyType
    place: object
    negate: bool


@dataclass(frozen=True)
class PWalrus:
    loc: Loc
    type: TpyType
    value: object
    target: PGlobalPlace | PNamePlace


# --- views --------------------------------------------------------------------

@dataclass(frozen=True)
class PViewName:
    """A view binding, its own holder."""
    loc: Loc
    type: TpyType
    name: str


@dataclass(frozen=True)
class PViewField:
    """The loan a view member stores, read whole."""
    loc: Loc
    type: TpyType
    place: PFieldPlace


@dataclass(frozen=True)
class PViewSlice:
    """An unstepped slice: its bounds are read, its whole receiver viewed."""
    loc: Loc
    type: TpyType
    bounds: tuple[object, ...]
    receiver: object


@dataclass(frozen=True)
class PViewCall:
    loc: Loc
    type: TpyType
    call: PCall


@dataclass(frozen=True)
class PElementValue:
    """An owned-leaf element place read in place; `view`: through a view of it."""
    loc: Loc
    type: TpyType
    element: PElementPlace
    view: bool


@dataclass(frozen=True)
class PViewOf:
    """A view of a source, rendered in place: `type` is the view the check
    asked for, `view` the view a holder of it is typed by (an owned leaf's
    family's, or a view-target coercion's own)."""
    loc: Loc
    type: TpyType
    view: TpyType | None
    source: object


# --- owned leaves -------------------------------------------------------------

@dataclass(frozen=True)
class POwnedPlace:
    """An owned-leaf binding or global: the storage it is or borrows."""
    loc: Loc
    type: TpyType
    place: PGlobalPlace | PNamePlace
    copy_may_raise: object


@dataclass(frozen=True)
class POwnedLiteral:
    """A literal; `borrowed`: static storage lent in place (a str or a
    borrow-form bytes literal), else a value built into storage."""
    loc: Loc
    type: TpyType
    value: object
    borrowed: bool


@dataclass(frozen=True)
class POwnedCall:
    """A call's owned-leaf result; `borrowed`: it may borrow what the call lends."""
    loc: Loc
    type: TpyType
    call: PCall
    borrowed: bool
    copy_may_raise: object


@dataclass(frozen=True)
class POwnedPassthrough:
    """A source's own storage read as its family's owned type; `view` is
    the family's view a lent operand is held through."""
    loc: Loc
    type: TpyType
    inner: object
    view: TpyType | None
    copy_may_raise: object


@dataclass(frozen=True)
class POwnedFormConvert:
    """A borrowed read copied into owned storage."""
    loc: Loc
    type: TpyType
    source: object
    copy_may_raise: object


@dataclass(frozen=True)
class POwnedField:
    loc: Loc
    type: TpyType
    place: PFieldPlace
    copy_may_raise: object


# --- tuples, Optionals, unions ------------------------------------------------

@dataclass(frozen=True)
class PTupleName:
    loc: Loc
    layout: THIRTupleLayout
    shape: MIRShape
    name: str


@dataclass(frozen=True)
class PTupleLiteral:
    """Each element in its member's form: a record a borrowed member names
    (a binding's place), a record an owned member builds, else a value."""
    loc: Loc
    layout: THIRTupleLayout
    shape: MIRShape
    elements: tuple[object, ...]


@dataclass(frozen=True)
class PAbsent:
    """An Optional left empty, or a union's absent alternative."""
    alternative: int | None = None


@dataclass(frozen=True)
class PWrapperLiteral:
    """An Optional or a union engaged with a literal payload (none for an
    absent union alternative); `alternative` is a union's."""
    loc: Loc
    alternative: int | None
    payload: TpyType | None
    value: object


@dataclass(frozen=True)
class PWrapperCopy:
    """Another Optional or union binding's whole value."""
    name: str


@dataclass(frozen=True)
class POptionalFieldCopy:
    """An Optional record field's inline payload, borrowed by a holder
    (`optional_to_ptr`)."""
    place: PFieldPlace


@dataclass(frozen=True)
class PWrapperCapture:
    """A record binding's holder, engaged as an Optional's or a union
    alternative's payload pointer."""
    name: str
    alternative: int | None = None


@dataclass(frozen=True)
class PWrapperValue:
    """A scalar value engaged as an Optional's or a union alternative's payload."""
    value: object
    alternative: int | None = None


@dataclass(frozen=True)
class PTemporary:
    """A record the temporary plan places (an argument's, a select's): its
    placement and scope, whether its storage starts empty (`optional`), the
    shapes of that storage and of the holder lending it, and what
    initializes it. As an operand it is that holder; `key` is the node its
    backing is published under."""
    key: object
    loc: Loc
    index: int
    scope: int
    optional: bool
    storage: MIRShape
    holder: MIRShape
    value: object


@dataclass(frozen=True)
class PAnchored:
    """An operand the temporary plan initializes temporaries at (a lazily
    evaluated operand, a select's emplacement): they are built first."""
    temporaries: tuple[PTemporary, ...]
    body: object

    @property
    def loc(self) -> Loc:
        return self.body.loc

    @property
    def type(self) -> TpyType:
        return self.body.type


@dataclass(frozen=True)
class PStatementTemporaries:
    """The temporaries a statement declares (each in its scope) and those it initializes."""
    declared: tuple[PTemporary, ...]
    initialized: tuple[PTemporary, ...]


@dataclass(frozen=True)
class POptionalOperand:
    """An Optional parameter's argument: a holder of a scalar payload built
    in the call's region (`scalar`; held inline when `shape` is a scalar
    wrapper), or of what a record payload points at."""
    loc: Loc
    shape: MIRShape
    scalar: bool
    value: object


# --- records ------------------------------------------------------------------

@dataclass(frozen=True)
class PRecordSource:
    """Record storage named in place: what a binding's holder reaches
    (`held`: that holder lends it), or a member place; `holder` is the
    shape of a holder borrowing it."""
    loc: Loc
    record: THIRBorrowedRecord
    place: object
    held: bool
    member: bool
    holder: MIRShape

    @property
    def type(self) -> TpyType:
        return self.record.type


@dataclass(frozen=True)
class PMemberArgument:
    """A member taking the operand of a constructor parameter."""
    name: str


@dataclass(frozen=True)
class PMemberComposed:
    """A member built by its own constructor into storage of the full
    expression, then moved in."""
    storage: MIRShape
    members: 'PMembers'


@dataclass(frozen=True)
class PMemberView:
    """A literal a view member stores: a holder of its static storage."""
    loc: Loc
    holder: MIRShape
    source: MIRRvalue


@dataclass(frozen=True)
class PMemberConstant:
    loc: Loc
    type: TpyType
    source: MIRRvalue


@dataclass(frozen=True)
class PMemberOptional:
    """An Optional record member: an Optional holder, empty or borrowing a
    payload its own constructor builds (`composed`: the payload's storage
    shape, its holder's, and its members)."""
    shape: MIRShape
    composed: 'tuple[MIRShape, MIRShape, PMembers] | None'


@dataclass(frozen=True)
class PMembers:
    """A construct's members, in order; the copy of an Optional member's
    payload (`payloads`) or a member initializer (`may_raise`) may raise."""
    members: tuple[object, ...]
    payloads: tuple[TpyType, ...]
    may_raise: bool


@dataclass(frozen=True)
class PConstruct:
    """A construct over one operand per constructor parameter."""
    loc: Loc
    type: TpyType
    arguments: tuple[tuple[str, object], ...]
    members: PMembers


@dataclass(frozen=True)
class PRecordCall:
    """A call handing over its owned record result."""
    loc: Loc
    type: TpyType
    call: PCall


@dataclass(frozen=True)
class PRecordMove:
    type: TpyType
    name: str


@dataclass(frozen=True)
class PRecordCopy:
    type: TpyType
    source: PRecordSource


@dataclass(frozen=True)
class PRecordTemporary:
    """A record built, handed over, copied or moved into storage of the full
    expression, for an operation that moves it out or lends it."""
    loc: Loc
    type: TpyType
    storage: MIRShape
    value: object


@dataclass(frozen=True)
class PLentTemporary:
    """A record temporary lent to a constructor through a readonly holder."""
    temporary: PRecordTemporary
    holder: MIRShape


@dataclass(frozen=True)
class POwnedArgument:
    """An owned-leaf argument: lent (`borrowing`), else the callee's own copy
    built into storage of the full expression."""
    loc: Loc
    type: TpyType
    borrowing: bool
    value: object


@dataclass(frozen=True)
class PContainerLiteral:
    """Owned container storage built from one operand per element (a dict's
    keys and values alternating), each in its member's form."""
    loc: Loc
    type: TpyType
    operands: tuple[object, ...]


@dataclass(frozen=True)
class PContainerMove:
    """Owned container storage moved into the temporary a callee takes over."""
    loc: Loc
    storage: MIRShape
    name: str


@dataclass(frozen=True)
class PMoveOut:
    """The body's own storage of a binding, returned itself."""
    name: str
    type: TpyType


# --- container views ----------------------------------------------------------

@dataclass(frozen=True)
class PSpanAlias:
    """Another holder of the same view type: aliased."""
    name: str
    readonly: bool


@dataclass(frozen=True)
class PSpanRegion:
    """A view binding of another type: a borrow of the region it views."""
    name: str
    readonly: bool


@dataclass(frozen=True)
class PSpanSlice:
    """An unstepped slice: its bounds are read, its receiver's region borrowed."""
    bounds: tuple[object, ...]
    receiver: PContainer
    readonly: bool


# --- borrowed results ---------------------------------------------------------

@dataclass(frozen=True)
class PBorrowedCall:
    loc: Loc
    call: PCall
    holder: MIRShape


@dataclass(frozen=True)
class PBorrowedField:
    loc: Loc
    place: PFieldPlace
    holder: MIRShape


@dataclass(frozen=True)
class PBorrowedSelect:
    loc: Loc
    cond: object
    then: object
    orelse: object
    holder: MIRShape


@dataclass(frozen=True)
class PBorrowedName:
    loc: Loc
    name: str


# --- full expressions and statement bodies ------------------------------------

@dataclass(frozen=True)
class PFullExpression:
    """One full expression; `needs_region`: it owns temporaries, which a
    region of its own ends."""
    loc: Loc
    body: object
    needs_region: bool

    @property
    def type(self) -> TpyType:
        return self.body.type


@dataclass(frozen=True)
class PCallStatement:
    """A call whose result is discarded (a void call)."""
    loc: Loc
    type: TpyType
    call: PCall


@dataclass(frozen=True)
class PElementStore:
    """`xs[i] = v` stored in place: the value in its member's form, then the element."""
    type: TpyType
    place: PElementPlace
    value: object


@dataclass(frozen=True)
class PSetItemCall:
    """`xs[i] = v` through the `__setitem__` stub call."""
    type: TpyType
    call: PCall


@dataclass(frozen=True)
class PPrint:
    type: TpyType
    arguments: tuple[object, ...]


@dataclass(frozen=True)
class POptionalFieldValue:
    """What an Optional record field takes: `PAbsent`, a copy of an
    Optional's payload (a place), or a record temporary moved in."""
    type: TpyType
    payload: TpyType
    value: object


# --- statements ---------------------------------------------------------------
# A statement's `placement` says where storage its binding owns lives: the
# body (True), the statement's scope (False), or no storage placement (None);
# `missing_placement` is the declaration whose storage needs a placement THIR
# did not give, refused where the emit pass reaches it. `inline_payload`: the
# wrapper holds its payload inline, so a write of it initializes or assigns
# the payload too (a declaration's binding shape says so: `scalar_wrapper`).
# `operation` is the statement THIR publishes a borrow operation under. A
# `*_scope` is the temporary plan scope a scope of the body opens.

@dataclass(frozen=True)
class PHoist:
    """A binding declared before the statement that assigns it; a scalar
    wrapper starts at its physical default (`default`: alternative, value)."""
    binding: PBinding
    default: tuple[int, object] | None


@dataclass(frozen=True)
class PDeclare:
    """A local and its initializer, written from the value's own form;
    storage the binding owns is the scope's (`placement`)."""
    loc: Loc
    binding: PBinding
    value: PFullExpression | None
    placement: bool | None


@dataclass(frozen=True)
class PContainerAlias:
    """A local holding a container place: a binding's, a field's, or the
    container a call returns by reference."""
    loc: Loc
    binding: PBinding
    source: PContainer
    operation: object


@dataclass(frozen=True)
class PNarrowAlias:
    """A narrowed union alternative's payload bound to a name; `aliased`:
    the binding is the payload in place (its alias source)."""
    loc: Loc
    binding: PBinding
    source: PUnionPayloadPlace
    aliased: bool


@dataclass(frozen=True)
class PUnionDeclare:
    loc: Loc
    binding: PBinding
    value: object
    placement: bool | None
    missing_placement: object | None


@dataclass(frozen=True)
class PUnionAssign:
    loc: Loc
    name: str
    value: object
    inline_payload: bool


@dataclass(frozen=True)
class PRecordBacking:
    """Record storage an Optional's payload points at, and the holder lending it."""
    loc: Loc
    storage: MIRShape
    holder: MIRShape


@dataclass(frozen=True)
class POptionalDeclare:
    """An Optional local: from its source, or a record built into its own
    backing (`backing`); `operation` when it borrows a field's payload."""
    loc: Loc
    binding: PBinding
    value: object
    placement: bool | None
    backing: PRecordBacking | None
    operation: object | None
    missing_placement: object | None


@dataclass(frozen=True)
class POptionalAssign:
    """An Optional local's value replaced: from its source, or a record
    built into new backing (`backing`) or into its payload in place."""
    loc: Loc
    name: str
    value: object
    backing: PRecordBacking | None
    in_place: bool
    inline_payload: bool


@dataclass(frozen=True)
class POwnedRecordDeclare:
    """A local owning its record storage, reached through its holder;
    `own_site`: storage of a hoisted backing, initialized at its own site."""
    loc: Loc
    binding: PBinding
    value: PFullExpression
    placement: bool | None
    own_site: bool
    missing_placement: object | None


@dataclass(frozen=True)
class POptionalRecordAssign:
    """A hoisted Optional record backing filled and its holder repointed."""
    loc: Loc
    name: str
    value: object


@dataclass(frozen=True)
class PReplacement:
    """A record holder's referent replaced: built into new storage
    (`storage`) or into the referent in place."""
    loc: Loc
    name: str
    value: PFullExpression
    storage: MIRShape | None


@dataclass(frozen=True)
class PBorrowBinding:
    """A holder bound or rebound to a borrow of a place (`deref`: through a
    tuple member's pointer)."""
    loc: Loc
    name: str
    binding: PBinding | None
    source: object
    deref: bool
    operation: object


@dataclass(frozen=True)
class PTupleAlias:
    """A local naming another tuple binding's own storage."""
    name: str
    source: str


@dataclass(frozen=True)
class PBorrowedBinding:
    """A holder bound or rebound to a borrowed result: a call's, a
    conditional's, or another holder's referent."""
    loc: Loc
    name: str
    binding: PBinding | None
    value: object
    operation: object


@dataclass(frozen=True)
class POwnedTupleDeclare:
    """A tuple local built in its own storage from its members (a record
    built in place, else a value); THIR places it in its scope (the check
    pass requires it)."""
    loc: Loc
    binding: PBinding
    value: PTupleLiteral


@dataclass(frozen=True)
class PAssign:
    """A place written from a value of its own form (`in_place`: the
    storage it holds is replaced in place); the place is evaluated first."""
    loc: Loc
    target: object
    value: PFullExpression
    in_place: bool


@dataclass(frozen=True)
class PScalarAssign:
    """A scalar written to a place evaluated within the full expression."""
    loc: Loc
    target: object
    value: PFullExpression


@dataclass(frozen=True)
class PViewMemberAssign:
    """A view member rebound to the loan a view holder holds."""
    loc: Loc
    target: PFieldPlace
    value: PFullExpression


@dataclass(frozen=True)
class PExpressionStatement:
    """A full expression evaluated for its effect: a value, a record
    temporary, a call, a print, an element store or a `__setitem__` call."""
    loc: Loc
    value: PFullExpression


@dataclass(frozen=True)
class PReturnHolder:
    """A returned view or container view: written into a result holder that
    outlives the full expression, whose temporaries it must not reach."""
    loc: Loc
    holder: MIRShape
    value: PFullExpression


@dataclass(frozen=True)
class POwnedReturn:
    """An owned result (`Own[...]` or an owned leaf): a binding's own
    storage (`PMoveOut`; a record's backing, `backing`) leaves the body,
    else a copy or a build into a `result` slot, written at `write_loc`."""
    loc: Loc
    value: PFullExpression
    result: MIRShape
    backing: bool
    write_loc: Loc


@dataclass(frozen=True)
class PReturnBorrowed:
    """A borrowed result: a holder of a record (`operation`: the return a
    borrow operation is published under) or a container place."""
    loc: Loc
    value: object
    operation: object | None


@dataclass(frozen=True)
class PReturnValue:
    loc: Loc
    value: PFullExpression | None


@dataclass(frozen=True)
class PIf:
    """An if; an elif chain (`elif_`: the else body is one PIf continuing
    it) flattens under a temporary plan. Its temporaries and the temporary
    scopes its arms and a continuing condition open."""
    loc: Loc
    condition: PFullExpression
    then: tuple[object, ...]
    orelse: tuple[object, ...]
    hoists: tuple[PHoist, ...]
    elif_: bool
    temporaries: PStatementTemporaries | None
    then_scope: int | None
    else_scope: int | None
    condition_scope: int | None


@dataclass(frozen=True)
class PWhile:
    """A while; a temporary plan scope per iteration (`iteration`) makes its
    condition's temporaries per-iteration storage."""
    loc: Loc
    condition: PFullExpression
    body: tuple[object, ...]
    orelse: tuple[object, ...]
    hoists: tuple[PHoist, ...]
    temporaries: PStatementTemporaries | None
    iteration: int | None
    loop_scope: int | None
    else_scope: int | None


@dataclass(frozen=True)
class PRangeLoop:
    """A range loop's bounds (no start: 0) and unit step; the target's own
    binding (None when hoisted); `direct`: the counter is the target itself."""
    loc: Loc
    hoists: tuple[PHoist, ...]
    start: object | None
    stop: object
    step: int
    binding: PBinding | None
    name: str
    direct: bool
    body: tuple[object, ...]
    orelse: tuple[object, ...]
    counter_scope: int | None
    loop_scope: int | None
    else_scope: int | None


@dataclass(frozen=True)
class PForEach:
    """A loop over a container place through an iterator (`iterator`: its
    shape); the target's own binding (None when hoisted)."""
    loc: Loc
    hoists: tuple[PHoist, ...]
    source: PContainer
    iterator: MIRShape
    binding: PBinding | None
    name: str
    body: tuple[object, ...]
    orelse: tuple[object, ...]
    loop_scope: int | None
    else_scope: int | None


@dataclass(frozen=True)
class PParamCopy:
    """The body's owned copy of a borrowed owned-leaf parameter it reassigns."""
    loc: Loc
    binding: PBinding
    copy_may_raise: object


@dataclass(frozen=True)
class PAppend:
    """`t += v` in place: it reads the old value and v, then replaces t."""
    loc: Loc
    target: object
    type: TpyType
    value: PFullExpression


@dataclass(frozen=True)
class PJump:
    """`continue` (`head`) or `break`."""
    loc: Loc
    head: bool


@dataclass(frozen=True)
class PParameter:
    """A parameter, and how long the storage its slot holds lives (`storage_duration`)."""
    binding: PBinding
    passing: object
    storage_duration: MIRStorageDuration | None


@dataclass(frozen=True)
class PBody:
    """A body: its receiver and parameters, the globals it reads, its
    statements; `planned`: the temporary plan is in use; what it returns
    and summarizes; a constructor's member initializers (`initializers`);
    the layouts of the records its storage has."""
    kind: MIRBodyKind
    return_type: TpyType
    void: bool
    receiver: PParameter | None
    parameters: tuple[PParameter, ...]
    globals: tuple[tuple[MIRGlobalId, PBinding], ...]
    statements: tuple[object, ...]
    planned: bool
    callees: tuple[MIRCallSummary, ...]
    result: THIRBorrowedRecord | None
    initializers: tuple[MIRFieldInitializer, ...] | None
    layouts: Mapping[TpyType, MIRRecordLayout]
