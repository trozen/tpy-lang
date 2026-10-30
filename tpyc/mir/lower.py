"""All-or-nothing lowering of scalar and record-storage THIR operations."""

from contextlib import contextmanager
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, fields, replace
from enum import Enum, auto
from typing import NamedTuple
from types import MappingProxyType

from ..identity_map import IdentityMap
from ..codegen_cpp.forms import LoopBinding
from ..type_def_registry import is_array
from ..parse import ResultForm, RebindStorage
from ..thir import nodes as th
from ..thir.frame import (
    FrameCaptureKind, THIRBranch, THIRFall, THIRFallOff, THIRFinallyExit, THIRForAdvance, THIRForIterSetup,
    THIRFrameReturn,
    THIRLeafSlot, THIRLoopSourceRegion, THIRMatchDispatch, THIRRegion, THIRSuspend, THIRWithRegion,
    THIRYieldSuspension, terminator_targets,
)
from ..thir.source import BindingConstruct, BindingRepr, Held
from ..thir.temp_plan import if_chain, validate_plan
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, container_view, converted_literal, declared_members, holds_loan, inline_member_type,
    leaf_constant, leaf_global, native_container_subject, native_container_type, optional_record_field,
    owned_constant, owned_leaf,
    owned_value_type, primitive_leaf, primitive_owned_leaf, record_type, storage_leaf, value_class,
    view_compatible, view_leaf,
)
from ..type_def_registry import ParamPassing, int_traits_of, record_info_of, zero_value_of
from ..typesys import (
    BOOL, INT32, VOID, NominalType, TpyType,
    NoneType, OptionalType, ReadonlyType, Representation, TupleType, VoidType,
    certified_primitive_comparison, holds_borrowing_view, passing_representation,
    return_representation, through_view, unwrap_own, unwrap_readonly, view_family_of, view_owned_leaf,
)
from .nodes import (
    MIRAlias, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant,
    MIRConstruct, MIRFunction, MIRGlobalId, MIRGoto, MIRIsAlternative, MIRIteratorAdvance, MIRIteratorHasNext,
    MIRIteratorInit, MIRIteratorRead, MIRMove, MIRNot, MIRNotCovered,
    MIROp, MIRStorageWriteMode, MIRPlace, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead,
    MIRStorageWrite, MIRReturn, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRStorageInit, MIRSuspend, MIRUnionExtract, function_body_kind, MIRWrap, MIRCopy,
)
from .value import (
    AGGREGATE_CLASSES, Declared, Local, MIRField, MIRFieldId, MIRHolding, MIRPosition, MIRRecordLayout, MIRRecords,
    MIRRole, MIRShape, MIRValidationError, admission_problem, lent_shape_problem, container_members, fact_shape,
    holder_form, layout_fact, loan_access, member_compatible, position_problem,
    shape_problem, subject_class, thir_member, value_shape, caller_storage,
)
from .coverage import MIRUnsupported, literal_type, plain as _plain, require as _require
from .definitions import (
    Invocation, MIRConstructorBody, MIRConstructorDefinition, MIRContainerDefinition, MIRDefinitions,
    MIRAggregateFacts, MIRHeldLayout, MIROwnedLeafDefinition, aggregate_facts, constructor_body,
    constructor_function,
    copy_may_raise, invocation_callee, invocation_record,
)
from .emit import (
    Fragment, MIRFallthroughError, MIRRegionStorageError, MIRValue, assemble, owned_leaf_shape, scalar_shape,
    view_holder_shape,
)
from .shapes import (
    inline_payload_holder, step, OPTIONAL_PAYLOAD, MIRContainerElements, MIRDeref, MIRTupleIndex, MIRUnionPayload,
    MIRWalk, Projection, object_prefix, sole_object, store_problem, walk,
)
from .call_contract import (
    BORROWING_PASSINGS, MIRCallSummary, MIRParameterBinding, MIRPathWalks, MIRSummaryResult, MIRSummaryState, binds_at,
    body_result, call_result, crossed_records,
    owned_aggregate_result, bound_params, parameter_binding, region_view, signature_params, signature_result,
    stub_protocol_argument, stub_summary, summary_problem, view_result,
)
from .validate import MIRDefiniteAssignmentError, MIRPresenceError, MIRRepeatedInitializationError, reaches_element
from .prepare import MIRPrepared, validated


def owner_display_name(owner: TpyType) -> str:
    """A method's owner by the name its user wrote: an enum's methods live
    on a companion record, which is spelled as the enum."""
    record = record_info_of(owner)
    return record.display_name if record is not None else owner.name


def _declared_const(stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> bool:
    """A declaration's const verdict: its binding's, for a record and a
    container alike (a container's pointer-slot declaration spells none)."""
    return stmt.binding.const if isinstance(stmt.binding, BindingRepr) else stmt.is_const


def _last_loc(stmts: tuple[th.THIRStmt, ...]) -> object:
    """A diagnostic names the end of a block by its last statement: the
    goto leaving the block carries the whole compound statement's span."""
    return stmts[-1].loc if stmts else None


def _literal(expr: th.THIRExpr) -> bool:
    return isinstance(expr, th.THIRLiteral) or (
        isinstance(expr, th.THIRCoerce) and isinstance(expr.expr, th.THIRLiteral))


def _user_call(expr: th.THIRExpr) -> bool:
    """A call `_Lowering.call` checks: a free-function call, or a method
    call THIR resolved to a user record method. A method stub's call has
    arms of its own."""
    return isinstance(expr, th.THIRCall) or isinstance(expr, th.THIRMethodCall) and expr.resolved_callee is not None


def _summary_writes(summary: MIRCallSummary) -> bool:
    """Whether a callee writes what its caller reaches: a parameter write,
    or a loan stored in an argument's object (a view member another operand
    may read)."""
    return bool(summary.writes) or any(type(t.holder) is int for t in summary.transfers)


def _value_result_form(expr: th.THIRCall | th.THIRMethodCall) -> set[str]:
    """`result_form` admitted as metadata when it carries no borrow fact: a
    declared callee's value-shaped result (`d.get("a", 0)` over int values)
    is a plain value. A BORROW / REFERENCE_VALUE / COPY verdict names what
    the result borrows or copies, which MIR does not model yet."""
    return ({"result_form"} if expr.result_form in (ResultForm.NOT_DECLARED, ResultForm.VALUE)
            else set())


def _root_name(place: th.THIRExpr) -> str | None:
    """The binding a place's chain of field accesses starts from."""
    root = place
    while isinstance(root, th.THIRFieldAccess):
        root = root.receiver
    return root.name if isinstance(root, th.THIRName) else "self" if isinstance(root, th.THIRSelf) else None


def _refuse_none_checked(node: th.THIRExpr, receiver: th.THIRExpr, what: str) -> None:
    """A receiver under its runtime None check is not modeled: the check may
    raise, and its referent is the Optional's payload. The reason names the
    representation, a whole `std::optional` or a nullable pointer."""
    if isinstance(receiver, th.THIRNoneCheck):
        kind = "optional" if receiver.value.form is th.Form.STORAGE else "pointer"
        raise MIRUnsupported(node, f"unproven {kind} {what}")


def _invocation_loc(expr: Invocation) -> object:
    """Where a call is: a base initializer, which carries no location of
    its own, is where its first argument is."""
    if isinstance(expr, th.THIRBaseInit):
        return expr.args[0].loc if expr.args else None
    return expr.loc


def _call_arguments(expr: Invocation) -> tuple[th.THIRExpr, ...]:
    """The arguments a call binds in parameter order: a method's receiver is parameter 0."""
    return (expr.receiver, *expr.args) if isinstance(expr, th.THIRMethodCall) else expr.args


def _aggregate_class(typ: TpyType) -> MIRClass | None:
    """The class of a local of declared type `typ` when it holds a record or
    a native container; None else."""
    cls = subject_class(typ)
    return cls if cls in AGGREGATE_CLASSES else None


def _aggregate_holder(typ: TpyType, const: bool) -> MIRShape:
    """A holder of the record or container a local, rebind or hoist of
    declared type `typ` binds, at the access its declaration states (bound
    `const`, or `readonly[...]`)."""
    subject = native_container_subject(typ)
    return value_shape(subject, access=loan_access(typ, subject_class(typ), Local(const=const)), role=MIRRole.LOAN)


def owned_leaf_role(expr: th.THIRExpr, summary: MIRCallSummary | None = None) -> MIRRole:
    """What a call result or a literal is to its owned-leaf value: STORAGE
    built for it (fresh), or a LOAN of storage elsewhere. The one site
    refining THIR's `Held`, which says FRESH or VALUE for both: a call's
    comes from its summary (the result may borrow what the call lends), a
    string or bytes literal's from its form (a borrow-form literal is static
    storage), any other from its `Source`. Whether a value is a place or an
    rvalue at all is the operation's."""
    if summary is not None:
        return MIRRole.STORAGE if summary.borrowed_result is None else MIRRole.LOAN
    if isinstance(expr, (th.THIRStrLiteral, th.THIRBytesLiteral)):
        return MIRRole.STORAGE if expr.form is th.Form.STORAGE else MIRRole.LOAN
    source = expr.source
    return MIRRole.LOAN if source is not None and source.held is Held.BORROWED else MIRRole.STORAGE


def _loan(fact: th.THIRBorrowedRecord) -> MIRShape:
    """A holder of the storage a borrow fact names, at the access the fact
    carries (decided where its source is named, `loan_access`)."""
    return value_shape(fact.type, access=fact.readonly, role=MIRRole.LOAN)


def _owned_optional(typ: TpyType) -> MIRShape:
    """An Optional storing a record of `typ` inline: storage that starts
    empty and that each fill selects (C++ `std::optional<T>`)."""
    if value_class(typ) is MIRClass.RECORD:
        return value_shape(OptionalType(typ), facts=th.THIROptionalLayout(th.THIROwnedRecord(typ)))
    # A container held inline: its own storage shape as the payload.
    return MIRShape(OptionalType(typ), MIRClass.OPTIONAL, MIRHolding.VALUE, False, (None, value_shape(typ)))


@dataclass(frozen=True, eq=False)
class _Temporary:
    """A record the temporary plan places (an argument's, a select's): its
    placement and scope, whether its storage starts empty (`optional`),
    its storage and the holder lending it, and the fragment of the record
    value that initializes it. `key` is the node its backing is published
    under."""
    key: object
    loc: object
    index: int
    scope: int
    optional: bool
    storage: MIRSlotId
    holder: MIRSlotId
    value: Fragment


class _Source(Enum):
    """The kinds of aggregate source `_Lowering.aggregate_place` tells apart."""
    HOLDER = auto()         # a name or `self`: what its holder reaches
    OPTIONAL_NAME = auto()  # a narrowed Optional record name: its payload
    MEMBER = auto()         # an aggregate member read in place
    CALL = auto()           # a user call's borrowed result


class _Bound(NamedTuple):
    """A name in scope: the slot it is, and the record storage it owns
    through that slot (an owned record local or `Own[R]` parameter, an
    Optional record backing)."""
    slot: MIRSlotId
    backing: MIRSlotId | None = None


class _Place(Enum):
    """The kinds of place expression `_Lowering.place` lowers (`place_kind`)."""
    GLOBAL = auto()
    NAME = auto()
    SELF = auto()
    FIELD = auto()
    ELEMENT = auto()
    UNION_PAYLOAD = auto()
    TUPLE_MEMBER = auto()


def place_kind(expr: th.THIRExpr) -> _Place | None:
    """The kind of place `expr` names, None when it names none: a global
    (a global name, a module variable), a local binding, `self`, a field, a
    subscript of a container (an element) or of a tuple (a member), a
    narrowed union's payload."""
    match expr:
        case th.THIRModuleVar():
            return _Place.GLOBAL
        case th.THIRName():
            return _Place.NAME if expr.global_binding is None else _Place.GLOBAL
        case th.THIRSelf():
            return _Place.SELF
        case th.THIRFieldAccess():
            return _Place.FIELD
        case th.THIRSubscript():
            return _Place.ELEMENT if expr.tuple_index is None else _Place.TUPLE_MEMBER
        case th.THIRNarrowedRead():
            return _Place.UNION_PAYLOAD
    return None


# The places each reader admits: a scalar value, an owned leaf, a view
# holder's source, a container view's source, an aggregate source (an
# element place is refused there: its lend raises nothing, so it would drop
# a bounds exit).
_SCALAR_PLACES = (_Place.GLOBAL, _Place.NAME, _Place.FIELD, _Place.ELEMENT, _Place.UNION_PAYLOAD,
                  _Place.TUPLE_MEMBER)
# A name and a field are scalar by their admission; any other place is checked.
_SCALAR_REFUSALS = {_Place.ELEMENT: "element read type mismatch",
                    _Place.UNION_PAYLOAD: "union scalar extraction required",
                    _Place.TUPLE_MEMBER: "scalar tuple projection required"}
_OWNED_PLACES = (_Place.GLOBAL, _Place.NAME, _Place.FIELD, _Place.ELEMENT)
_OWNED_REFUSALS = {_Place.FIELD: "owned-leaf field type mismatch", _Place.ELEMENT: "owned-leaf element type mismatch"}
_VIEW_PLACES = (_Place.NAME, _Place.FIELD, _Place.ELEMENT)
_SPAN_PLACES = (_Place.NAME,)
_AGGREGATE_PLACES = (_Place.NAME, _Place.SELF, _Place.FIELD)
# The places a field is read through: any record place but a global's; a
# binding holding a record is one by its holder, any other is checked.
_RECEIVER_PLACES = (_Place.NAME, _Place.SELF, _Place.FIELD, _Place.ELEMENT, _Place.UNION_PAYLOAD,
                    _Place.TUPLE_MEMBER)
_RECEIVER_REFUSALS = {_Place.FIELD: "field needs record storage", _Place.UNION_PAYLOAD: "field needs union record",
                      _Place.ELEMENT: "field needs record element",
                      _Place.TUPLE_MEMBER: "field needs tuple reference"}
# A binding of these classes is its own holder (`MIRValue.held`).
_HOLDER_BINDINGS = AGGREGATE_CLASSES | {MIRClass.CONTAINER_VIEW, MIRClass.VIEW}


def _family_view(typ: TpyType) -> TpyType | None:
    """The view an owned leaf's family lends it through; None outside a view family."""
    family = view_family_of(typ)
    return family.view_type if family is not None else None


def _leaf_declarations(stmts: tuple[th.THIRStmt, ...]) -> set[str]:
    """The names statements declare themselves, at any depth: a
    declaration's, a narrowing alias's, a hoisted binding's, and the target
    of a loop that does not hoist it."""
    names: set[str] = set()
    pending = list(stmts)
    while pending:
        stmt = pending.pop()
        match stmt:
            case th.THIRVarDecl(name=name) | th.THIRPtrLocalDecl(name=name) | th.THIRNarrowAlias(alias=name):
                names.add(name)
            case th.THIRForEach() | th.THIRForRange() if not stmt.hoist_loop_var:
                names.add(stmt.var)
        names.update(decl.name for decl in getattr(stmt, "hoist_decls", ()))
        for member in fields(stmt):
            value = getattr(stmt, member.name)
            if isinstance(value, tuple):
                pending.extend(item for item in value if isinstance(item, th.THIRStmt))
    return names


def _region_feature(region: THIRRegion) -> str:
    """The construct a frame region is open for, as a refusal names it."""
    if isinstance(region, THIRWithRegion):
        return "with across a suspension"
    if isinstance(region, THIRLoopSourceRegion):
        return "loop across a suspension"
    return "try across a suspension"


# The literal-into-slot coercions that render the inner literal directly in
# the slot's type; any other coercion converts a value.
_LITERAL_COERCIONS = frozenset({"int_literal_to_fixed_int", "float_literal_to_float", "float_literal_to_float32"})
# The print forms that stream a scalar leaf through the runtime's formatter.
_SCALAR_PRINT_FORMS = frozenset({th.PrintForm.RAW, th.PrintForm.INT8, th.PrintForm.BOOL,
                                 th.PrintForm.FLOAT, th.PrintForm.FLOAT32})
# The print forms that stream an owned leaf through the runtime: its own
# `operator<<`, or the bytes printer.
_OWNED_PRINT_FORMS = frozenset({th.PrintForm.RAW, th.PrintForm.BYTES})


class _Lowering:
    """MIR's lowering: walks a THIR body once, refusing what MIR does not
    model, and builds the body's MIR as it goes -- each expression and each
    statement a fragment (`emit.Fragment`) spliced into the body."""

    def __init__(self, fn: th.THIRFunction | None, body: MIRBodyId, definitions: MIRDefinitions,
                 summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None,
                 constructor: MIRConstructorBody | None = None,
                 frame: th.THIRResumableLeaves | None = None) -> None:
        # A generator's body is its published frame's leaves (`frame_body`),
        # read under the frame's header; it has no THIRFunction.
        assert (fn is None) is (frame is not None)
        self.fn = fn
        self.frame = frame
        self.header = th.body_header(fn if fn is not None else frame)
        # What a refusal of the whole body names: the function, or the generator's def.
        self.node: object = fn if fn is not None else frame.frame.func
        self.temp_plan = fn.temp_plan if fn is not None else None
        # The body's own statement list; a frame's statements are its blocks' leaves.
        self.top_statements: tuple[th.THIRStmt, ...] = fn.body if fn is not None else ()
        # A frame declares every local at entry and assigns it after: each may be reassigned.
        self.reassigned_locals = (fn.layout.reassigned_locals if fn is not None
                                  else frozenset(local.name for local in frame.frame.locals))
        # A generator completes without a value: what it hands out, it yields.
        self.return_type = body_result(fn) if fn is not None else VOID
        # A constructor's body builds and returns its record (`constructor_entry`).
        self.constructor = constructor
        self.constructor_result: MIRSlotId | None = None
        self.parameters = {p.name for p in self.header.params}
        # The body has a temporary plan in use, and the temporary plan scope
        # the statements being checked run in.
        self.planned = self.temp_plan is not None and bool(self.temp_plan.placements)
        self.temp_now = 0
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()
        self.definitions = definitions
        self.records: dict[NominalType, MIRConstructorDefinition | MIRConstructorBody | MIROwnedLeafDefinition
                           | MIRHeldLayout] = {}
        # The container types whose definitions the body registered (`define`).
        self.containers: set[NominalType] = set()
        # The body's root fragment: every slot and block is built into it.
        self.mir = Fragment(body=body, layouts=self.layouts, copy_may_raise=self.copy_may_raise)
        # The owned sums of hoisted pointer locals, built empty at the body's
        # top where C++ declares them (`site_backing`).
        self.entry_backings: list[MIRSlotId] = []
        # The owned sum of each THIR rebind slot a reseat fills, built empty
        # at the body-level statement THIR anchors it at (`anchored_slots`).
        self.rebind_slots: dict[th.THIRRebindSlot, MIRSlotId] = {}
        self.unanchored: set[MIRSlotId] = set()
        # The targets a `continue` and a `break` jump to, innermost loop last.
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []
        # The borrowed result the body returns: a record, a container or a view.
        self.result: th.THIRBorrowedRecord | None = None
        # Bindings holding their own record or container storage (an owning
        # local, an `Own[...]` parameter), and those never reseated (a reseat
        # may retarget the holder at another backing). A lent parameter is in
        # neither.
        self.owned_locals: set[str] = set()
        self.fixed_owned: set[str] = set()
        self.tuple_roots: dict[str, str] = {}
        self.body_declarations = {id(stmt) for stmt in self.top_statements if isinstance(stmt, th.THIRVarDecl)}
        self.globals: dict[MIRGlobalId, th.THIRGlobalBinding] = {}
        self.global_slots: dict[MIRGlobalId, MIRSlotId] = {}
        self.range_counters: set[str] = set()
        # An `Own[...]` record or container result: the body's own storage moved out.
        self.owned_aggregate_result: NominalType | None = None
        self.iteration_references: set[str] = set()
        self.summaries = summaries if summaries is not None else {}
        self.calls: IdentityMap[th.THIRCall | th.THIRMethodCall, MIRCallSummary] = IdentityMap()
        # Each user call's borrowed result, at the access its receiver is bound at.
        self.call_results: IdentityMap[th.THIRCall | th.THIRMethodCall, th.THIRBorrowedRecord | None] = IdentityMap()
        self.call_summaries: IdentityMap[th.THIRCall | th.THIRMethodCall,
                                         tuple[MIRCallSummary, MIRPathWalks] | str] = IdentityMap()
        self.stub_summaries: dict[th.THIRStubIdentity, MIRCallSummary] = {}
        self.argument_temporaries: IdentityMap[th.THIRArgTemp, _Temporary] = IdentityMap()
        self.select_temporaries: IdentityMap[th.THIRSlotEmplace, _Temporary] = IdentityMap()
        # The nodes whose temporary-plan initializations are already built in front of them.
        self.anchored_nodes: IdentityMap[object, bool] = IdentityMap()
        # The leaf type each checked expression has in MIR; a number literal's
        # comes from its context, not its node.
        self.types: IdentityMap[th.THIRExpr, TpyType] = IdentityMap()
        # The slot each name in scope is, decided once where it is declared.
        self.scope: dict[str, _Bound] = {}
        # The binding each parameter slot was built from, receiver first.
        self.parameter_bindings: list[MIRParameterBinding] = []
        if constructor is not None:
            # The record under construction has its certificate here, whether
            # or not a caller may construct through its definition.
            self.records[constructor.layout.type] = constructor

    def layouts(self) -> MIRRecords:
        """The layouts of the records the body has registered so far, with
        the declared fields of every record."""
        return MIRRecords({t: d.layout for t, d in self.records.items()}, self.definitions.fields, self.containers)

    def fragment(self) -> Fragment:
        return self.mir.fragment()

    def valued(self, value: MIRValue) -> Fragment:
        """A fragment of nothing but a value."""
        f = self.mir.fragment()
        f.value = value
        return f

    def bind(self, name: str, shape: MIRShape, backing: MIRShape | None = None, *, loc: object = None,
             backing_kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
             alias_source: MIRPlace | None = None) -> _Bound:
        """A local's slot (and the record storage it owns), placed where its
        declaration is built."""
        storage = self.mir.record(backing, backing_kind, loc=loc) if backing is not None else None
        bound = _Bound(self.mir.record(shape, MIRSlotKind.LOCAL, name, loc=loc, alias_source=alias_source), storage)
        self.scope[name] = bound
        return bound

    def alias(self, name: str, source: str) -> None:
        """A name for another binding's own slot."""
        self.scope[name] = self.scope[source]

    def bound(self, name: str) -> MIRShape | None:
        bound = self.scope.get(name)
        return self.mir.records[bound.slot].shape if bound is not None else None

    def slot_of(self, name: str) -> MIRSlotId:
        return self.scope[name].slot

    def holder(self, name: str | None) -> MIRShape | None:
        """The shape of a name's slot when it holds a record or a container
        elsewhere (`borrowed_aggregate`): the aggregate and the holder's access."""
        shape = self.bound(name) if name is not None else None
        return shape if shape is not None and shape.borrowed_aggregate else None

    def wraps(self, name: str, cls: MIRClass) -> bool:
        """A name bound as an Optional, a union or a tuple."""
        shape = self.bound(name)
        return shape is not None and shape.cls is cls

    def optional_is(self, name: str, layout: th.THIROptionalLayout) -> bool:
        """Whether binding `name` is the Optional THIR's layout fact lays out."""
        shape = self.bound(name)
        return (shape is not None and shape.cls is MIRClass.OPTIONAL and isinstance(layout, th.THIROptionalLayout)
                and shape == value_shape(shape.type, facts=layout))

    def union_is(self, name: str, layout: th.THIRUnionLayout) -> bool:
        """Whether binding `name` is the union THIR's layout fact lays out."""
        shape = self.bound(name)
        return (shape is not None and isinstance(layout, th.THIRUnionLayout)
                and shape == value_shape(layout.type, facts=layout))

    def holds(self, name: str, fact: th.THIRBorrowedRecord) -> bool:
        held = self.holder(name)
        return held is not None and held == thir_member(fact)

    def view_bound(self, name: str) -> bool:
        """A view holder: a view parameter, local or loop target."""
        shape = self.bound(name)
        return shape is not None and shape.cls is MIRClass.VIEW

    def container_view_access(self, name: str) -> bool | None:
        """A container view holder's access (readonly when its elements or
        its source are), None for any other binding."""
        shape = self.bound(name)
        return shape.readonly if shape is not None and shape.container_view_holder else None

    def owned_leaf_bound(self, name: str) -> bool | None:
        """An owned-leaf binding: True for storage the body owns (a local, a
        by-value parameter, a parameter's copy), False for a borrow of
        storage outside it (a const-ref parameter, a loop target), None
        for any other binding."""
        shape = self.bound(name)
        if shape is None:
            return None
        return True if shape.leaf_storage else False if shape.owned_leaf_borrow else None

    def payload_alias(self, name: str) -> bool:
        """A narrowed union's scalar payload, aliased in place."""
        shape = self.bound(name)
        return shape is not None and shape.holding is MIRHolding.ALIAS

    def name_place(self, name: str, loc: object, *projections: object) -> Fragment:
        """A fragment denoting a binding's slot, or a place under it."""
        return self.valued(MIRValue(MIRPlace(self.slot_of(name), projections), loc=loc))

    def global_place(self, expr: th.THIRName | th.THIRModuleVar | th.THIRWalrus) -> MIRPlace:
        fact = expr.global_binding
        return MIRPlace(self.global_slots[MIRGlobalId(fact.module, fact.name)])

    def admitted_place(self, expr: th.THIRExpr, kinds: tuple[_Place, ...]) -> _Place | None:
        """The kind of place `expr` is when a reader admitting `kinds` reads
        it as a place; a subscript is an element place only of a container."""
        kind = place_kind(expr)
        if kind not in kinds or kind is _Place.ELEMENT and not self.container_receiver(expr.receiver):
            return None
        return kind

    def place(self, expr: th.THIRExpr) -> Fragment:
        """A place expression lowered once, as its place (`place_kind`): a
        global's storage, the storage a binding (`self` included) is or
        reaches -- a narrowed Optional's payload --, a narrowed union's
        payload, a field, a container element (raising as its index or key
        may), a tuple member (`binding_place` for a binding). The reader
        admits it at its position first."""
        match place_kind(expr):
            case _Place.GLOBAL:
                _plain(expr, {"cpp", "global_binding"} if isinstance(expr, th.THIRModuleVar)
                       else {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
                root = self.global_place(expr)
            case _Place.NAME | _Place.SELF:
                return self.binding_place("self" if isinstance(expr, th.THIRSelf) else expr.name, expr.loc)
            case _Place.UNION_PAYLOAD:
                self.inline_union(expr)
                extraction = expr.union_extraction
                root = MIRPlace(self.slot_of(extraction.source), (MIRUnionPayload(extraction.alternative),))
            case _Place.FIELD:
                return self.field(expr)[1]
            case _Place.ELEMENT:
                return self.element_access(expr)
            case _Place.TUPLE_MEMBER:
                return self.projection(expr)[1]
            case _:
                raise AssertionError("not a place expression")
        return self.valued(MIRValue(MIRPlace(root.root, root.projections + self.mir.storage_steps(root)),
                                    loc=expr.loc))

    def binding_place(self, name: str, loc: object) -> Fragment:
        """The storage binding `name` (`self` included) is or reaches -- a
        narrowed Optional's payload. A binding of a reference type is its
        own holder (`held`); an owned leaf's storage is what a return moves
        out (`storage`)."""
        slot, shape = self.slot_of(name), self.bound(name)
        root = MIRPlace(slot, (OPTIONAL_PAYLOAD,) if shape.cls is MIRClass.OPTIONAL else ())
        return self.valued(MIRValue(MIRPlace(root.root, root.projections + self.mir.storage_steps(root)),
                                    loc=loc, held=slot if shape.cls in _HOLDER_BINDINGS else None,
                                    storage=slot if shape.leaf_storage else None))

    def layout_of(self, typ: NominalType) -> MIRRecordLayout | None:
        """The layout of record or owned-leaf storage of type `typ` the
        facts are read from: its verified definition's, else the one the
        body registered."""
        definition = self.definitions.records.get(typ)
        if isinstance(definition, MIRConstructorDefinition):
            return definition.layout
        registered = self.records.get(typ)
        return registered.layout if registered is not None else None

    def facts(self, typ: NominalType) -> MIRAggregateFacts:
        """The copy and move facts of record or container storage of type
        `typ` (`aggregate_facts`); storage whose layouts are missing neither
        copies nor moves."""
        found = aggregate_facts(typ, self.layout_of)
        return found if found is not None else MIRAggregateFacts(False, False, True)

    def copyable(self, typ: NominalType) -> bool:
        return self.facts(typ).copyable

    def movable(self, typ: NominalType) -> bool:
        return self.facts(typ).movable

    def copy_may_raise(self, shape: MIRShape) -> bool:
        return copy_may_raise(shape, self.facts)

    def wrapper_copy(self, source: MIRPlace, target: MIRShape) -> MIRCopy:
        """A sum or tuple copied member by member into a value of `target`:
        raising as copying its storage members may, or as reaching an
        element does."""
        return MIRCopy(source, self.copy_may_raise(target) or reaches_element(source))

    # --- conversions of a value ----------------------------------------------------

    def holding(self, v: Fragment, *, raises: bool = True) -> Fragment:
        """`v`'s value as a holder slot (`Fragment.lend`), for an operation that takes one."""
        g = self.fragment()
        g.value = MIRValue(g.lend(v, raises=raises), loc=v.value.loc)
        return g

    def fresh_boundary(self, expr: th.THIRExpr, v: Fragment) -> None:
        """Fresh storage lent or viewed lives in its full expression: refused outside one."""
        if v.value.fresh:
            _require(expr, self.mir.active_temporaries is not None, "owned temporary needs full-expression boundary")

    def operand(self, expr: th.THIRExpr, v: Fragment) -> Fragment:
        """A view or owned-leaf value (`lent_value`) as the holder an operation
        reads: fresh storage materialized in the full expression and lent.
        An owned leaf's holder is one of the expression's type, though a
        passthrough's slot is typed by its family's view."""
        self.fresh_boundary(expr, v)
        if v.value.fresh:
            v.value = MIRValue(v.materialize(v.value, expr), loc=v.value.loc)
        g = self.holding(v)
        typ = self.types[expr]
        if not view_leaf(typ):
            g.value = MIRValue(g.value.value, owned_leaf_shape(typ, MIRRole.LOAN), g.value.loc)
        return g

    def owned_sink(self, expr: th.THIRExpr) -> Fragment:
        """An owned-leaf expression as what owned storage of its type takes (`Fragment.sink`)."""
        v = self.owned_value(expr)
        # A borrow-form bytes literal is a static span; a storage-form one builds a buffer.
        _require(expr, not isinstance(expr, th.THIRBytesLiteral) or expr.form is th.Form.STORAGE,
                 "borrowed bytes literal at an owning sink")
        return v.sink(owned_leaf_shape(self.types[expr], MIRRole.STORAGE))

    # --- calls ------------------------------------------------------------------

    def callee_of(self, expr: Invocation) -> tuple[th.THIRResolvedCallee | None, NominalType | None]:
        """The callee a call goes through (`invocation_callee`); a
        construction's or base initializer's record definition is
        registered with the body's layouts, or refused."""
        typ = invocation_record(expr)
        if typ is not None:
            _require(expr, isinstance(self.define(expr, typ), MIRConstructorDefinition),
                     "unsupported record initializer")
        return invocation_callee(expr, self.definitions)

    def call_summary(self, expr: Invocation) -> tuple[MIRCallSummary, MIRPathWalks] | str:
        """The finalized known summary a user call goes through, checked
        against the signature the call publishes, and the walks of its
        paths -- the one decision of what the call writes and returns,
        made once per call -- or why it has none."""
        if expr not in self.call_summaries:
            self.call_summaries[expr] = self._call_summary(expr)
        return self.call_summaries[expr]

    def _call_summary(self, expr: Invocation) -> tuple[MIRCallSummary, MIRPathWalks] | str:
        callee, _ = self.callee_of(expr)
        if not isinstance(callee, th.THIRResolvedCallee):
            return "call needs resolved ordinary callee"
        entry = self.summaries.get(callee.identity)
        if entry is None or entry.state is not MIRSummaryState.KNOWN:
            return "call needs finalized known summary"
        summary = entry.summary
        # The signature the call publishes and the summary's bindings must be one fact.
        if callee.signature.passings is None:
            return "signature passings unpublished"
        if not (isinstance(summary, MIRCallSummary)
                and callee.signature.passings == tuple(b.passing for b in summary.parameters)):
            return "signature passings mismatch"
        walks: MIRPathWalks = {}
        if summary_problem(summary, self.definitions.layouts, walks) is not None or summary.callee != callee:
            return "call summary signature or contract mismatch"
        return summary, walks

    def call(self, expr: Invocation, *, temporary: bool = False) -> Fragment:
        """A call through its callee's summary, parameter by parameter over
        `_call_arguments`: a free-function call, a resolved user method
        call rendered as the plain member call (a pointer receiver spells
        its access `->`), a construction `R(...)` calling its record's
        constructor, or a base initializer calling its base's, whose
        result initializes the base's fields of the record being built
        (`MIRCall.initializes`). `temporary`: the result is materialized in
        storage of its full expression."""
        if isinstance(expr, th.THIRCall) and expr.stub_callee is not None:
            return self.stub_call(expr)
        storage = {"full_expression_storage"} if temporary else set()
        if isinstance(expr, th.THIRMethodCall):
            _refuse_none_checked(expr, expr.receiver, "method receiver")
            _plain(expr, {"receiver", "method_cpp", "args", "is_arrow", "resolved_callee", "receiver_access"}
                   | storage)
        elif isinstance(expr, th.THIRCtorCall):
            _plain(expr, {"type_cpp", "args"} | storage)
        elif isinstance(expr, th.THIRCall):
            _plain(expr, {"callee", "args", "callee_cpp", "resolved_callee"} | storage)
        callee, initializes = self.callee_of(expr)
        found = self.call_summary(expr)
        _require(expr, not isinstance(found, str), found)
        summary, walks = found
        result = call_result(callee, None)
        owned = owned_value_type(signature_result(callee.signature))
        if result is not None and view_leaf(result.type):
            pass
        elif result is not None:
            # A record or container returned by reference: a holder of its declared type.
            problem = lent_shape_problem(signature_result(callee.signature), result)
            _require(expr, problem is None, problem)
            self.register(expr, value_shape(result.type))
        elif owned is not None:
            self.leaf_layout(expr, owned)
        arguments = _call_arguments(expr)
        # Every record whose fields a path the callee writes, returns or
        # stores a loan under reads -- the record an argument binds, or a
        # member's -- comes with its verified definition.
        for record in crossed_records(
                summary, walks,
                lambda parameter: self.bound_record(arguments[parameter]) if parameter < len(arguments) else None):
            self.record_layout(expr, record)
        # An `Own[T]` return hands the caller a plain T; a base's constructor
        # builds part of the record a construction or a constructor builds.
        record = owned_aggregate_result(signature_result(callee.signature)) if result is None else None
        built = invocation_record(expr)
        _require(expr, (built is not None and record in (built, initializes) if built is not None
                        else unwrap_readonly(expr.result_type) == result.type if result is not None
                        else expr.result_type == owned if owned is not None
                        else expr.result_type == record if record is not None
                        else expr.result_type == signature_result(callee.signature))
                 and len(_call_arguments(expr)) == len(summary.parameters),
                 "incomplete constructor arguments" if built is not None and len(_call_arguments(expr)) < len(
                     summary.parameters) else "call signature mismatch")
        # The access parameter 0 is bound at: a follows-receiver result takes it.
        receiver_readonly = False
        f = self.fragment()
        slots: list[MIRSlotId] = []
        for index, (arg, binding) in enumerate(zip(_call_arguments(expr), summary.parameters)):
            argument = self.argument(binding, arg)
            if index == 0 and binding.cls is MIRClass.RECORD and not binding.storage:
                # Only an argument temporary binds readonly whatever C++ picks (narrower, never wider).
                held = argument.shape_of()
                # The binding's access must be the one C++ picks the overload
                # by; a follows-receiver result is bound at it.
                _require(arg, isinstance(arg, th.THIRArgTemp) or not isinstance(expr, th.THIRMethodCall)
                         or expr.receiver_access == th.THIRBorrowedRecord(held.type, held.readonly),
                         "call receiver access disagrees with its binding")
                receiver_readonly = held.readonly
            slots.append(f.splice(argument))
        self.argument_order_rule(expr, _call_arguments(expr))
        if initializes is not None:
            # The base whose fields the call initializes is checked against its layout.
            self.record_layout(expr, initializes)
        return self.call_value(f, expr, summary, slots, receiver_readonly, _invocation_loc(expr), built=built,
                               initializes=initializes)

    def call_value(self, f: Fragment, node: Invocation | th.THIRSetItem,
                   summary: MIRCallSummary, arguments: list[MIRSlotId], receiver_readonly: bool,
                   loc: object, *, built: NominalType | None = None,
                   initializes: NominalType | None = None) -> Fragment:
        """The call `node` makes over its argument slots (a method's receiver
        is parameter 0), its result's access decided once (`result_at`) and
        its shape stated (`result_shape`): the record a construction builds
        (`built`), whose base's fields its callee initializes when its
        result is that base's (`initializes`)."""
        self.calls[node] = summary
        result = self.call_results[node] = summary.result_at(receiver_readonly)
        call = MIRCall(summary, tuple(arguments), not summary.normal_return_only, loc,
                       isinstance(node, th.THIRMethodCall), initializes)
        shape = value_shape(built) if built is not None else self.result_shape(node, summary, result)
        f.value = MIRValue(call, shape, loc)
        return f

    def result_shape(self, node: Invocation | th.THIRSetItem, summary: MIRCallSummary,
                     result: th.THIRBorrowedRecord | None) -> MIRShape:
        """The shape of a call's result: an owned leaf fresh or lent
        (`owned_leaf_role`); a borrowed result at its access -- a view's
        holder, a container's or a container view's region, a record's
        pointer --; an `Own[R]` record's storage; else a scalar."""
        returns = signature_result(summary.callee.signature)
        if (owned := owned_value_type(returns)) is not None:
            return owned_leaf_shape(owned, owned_leaf_role(node, summary))
        if result is None:
            record = owned_aggregate_result(returns)
            return value_shape(record) if record is not None else scalar_shape(unwrap_own(returns))
        if view_leaf(result.type):
            return view_holder_shape(result.type)
        return _loan(result)

    def stub_call(self, expr: th.THIRCall) -> Fragment:
        """A call to a stub, summarized from its declaration alone
        (`call_contract.stub_summary`); its arguments follow the user-call
        rows, and a protocol parameter admits only a builtin leaf argument,
        whose dispatch runs the stub's own runtime code."""
        _plain(expr, {"callee", "args", "native_name", "cpp_template", "callee_cpp", "stub_callee", "constructs"}
               | _value_result_form(expr))
        callee = expr.stub_callee
        _require(expr, expr.resolved_callee is None, "call has both a resolved and a stub callee")
        # Calls of one stub share one summary object, as the body's summary table holds them.
        summary = self.stub_summaries.get(callee.identity)
        if summary is None:
            summary = stub_summary(callee)
            if isinstance(summary, str):
                raise MIRUnsupported(expr, summary)
            self.stub_summaries[callee.identity] = summary
        _require(expr, summary.callee == callee, "inconsistent stub callee facts")
        signature = callee.signature
        owned = owned_value_type(signature_result(signature))
        if owned is not None:
            self.leaf_layout(expr, owned)
        _require(expr, (expr.result_type == owned if owned is not None
                        else expr.result_type == signature_result(signature))
                 and len(expr.args) == len(summary.parameters), "call signature mismatch")
        f = self.fragment()
        slots: list[MIRSlotId] = []
        for arg, binding in zip(expr.args, summary.parameters):
            if binding.protocol:
                # A view dispatches to the runtime code of the leaf it views.
                read = through_view(arg.result_type)
                _require(arg, stub_protocol_argument(read), "stub protocol argument is not a builtin leaf")
                if self.container_receiver(arg):
                    # A container (or a view of one) is read whole by the
                    # stub's own runtime code (`len`): no element is lent
                    # past the call.
                    _require(arg, callee.contract is th.THIRStubContract.PURE,
                             "stub protocol parameter needs a pure contract")
                    _require(arg, owned is None and view_result(
                        signature_result(signature), signature.return_representation) is None,
                             "stub result may borrow a container argument")
                    slots.append(f.lend(self.container_place(arg), raises=False))
                    self.writes[arg] = False
                    continue
                if owned_leaf(read):
                    slots.append(f.splice(self.operand(arg, self.lent_value(arg))))
                    continue
                # An owned-leaf or view result may borrow what the parameter
                # binds; a scalar argument has no storage to borrow.
                _require(arg, owned is None and view_result(
                    signature_result(signature), signature.return_representation) is None,
                         "stub result may borrow a scalar argument")
            slots.append(f.splice(self.argument(binding, arg)))
        self.argument_order_rule(expr, expr.args)
        return self.call_value(f, expr, summary, slots, False, expr.loc)

    def method_call(self, expr: th.THIRMethodCall, *, view: bool = False) -> Fragment:
        """A call to a native container's method stub, summarized from its
        declaration (`call_contract.stub_summary`): the receiver place is
        parameter 0, lent whole; the arguments follow the stub-call rows.
        With `view`, the result is a container view (`d.keys()`): a holder
        of the receiver's elements region, readonly when the view's elements
        or the receiver are."""
        if view:
            _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        _refuse_none_checked(expr, expr.receiver, "method receiver")
        _plain(expr, {"receiver", "method_cpp", "args", "native_function_name", "cpp_template", "stub_callee"}
               | _value_result_form(expr))
        callee = expr.stub_callee
        _require(expr, isinstance(callee, th.THIRStubCallee) and callee.receiver, "invalid stub callee")
        summary = self.stub_summaries.get(callee.identity)
        if summary is None:
            summary = stub_summary(callee)
            if isinstance(summary, str):
                raise MIRUnsupported(expr, summary)
            self.stub_summaries[callee.identity] = summary
        _require(expr, summary.callee == callee, "inconsistent stub callee facts")
        signature = callee.signature
        owned = owned_value_type(signature_result(signature))
        if owned is not None:
            self.leaf_layout(expr, owned)
        # A result borrowing the receiver (`setdefault`) needs a holder of its
        # region; a container view's is the view holder.
        result = summary.borrowed_result
        _require(expr, result is None if not view else result is not None and container_view(result.type),
                 "borrowed method result")
        # An `Own[T]` result hands the caller a plain T.
        _require(expr, (expr.result_type == owned if owned is not None
                        else expr.result_type == unwrap_own(signature_result(signature)))
                 and len(expr.args) + 1 == len(summary.parameters), "call signature mismatch")
        place = self.container_place(expr.receiver)
        shape = place.shape_of()
        f = self.fragment()
        slots = [f.lend(place, raises=False)]
        receiver = summary.parameters[0]
        # A pure method reads its receiver whatever its declared access (the
        # mutable clone of an `@auto_readonly` accessor).
        _require(expr, receiver.type == shape.type and (receiver.readonly or not shape.readonly
                                                         or callee.contract is th.THIRStubContract.PURE),
                 "call container argument mismatch")
        for arg, binding in zip(expr.args, summary.parameters[1:]):
            if binding.protocol:
                read = through_view(arg.result_type)
                _require(arg, stub_protocol_argument(read) and not self.container_receiver(arg),
                         "stub protocol argument is not a builtin leaf")
                if owned_leaf(read):
                    slots.append(f.splice(self.operand(arg, self.lent_value(arg))))
                    continue
            slots.append(f.splice(self.argument(binding, arg)))
        self.argument_order_rule(expr, expr.args)
        if not view:
            return self.call_value(f, expr, summary, slots, shape.readonly, expr.loc)
        self.register_container(expr, result.type)
        return self.lent_result(expr, self.call_value(f, expr, summary, slots, shape.readonly, expr.loc))

    def lent_result(self, expr: th.THIRExpr, call: Fragment) -> Fragment:
        """A lowered call's borrowed result, lent to a holder: the place of
        what the holder points at (or the region a view holds)."""
        self.writes[expr] = False
        c = self.fragment()
        holder = c.lend(call)
        c.value = MIRValue(MIRPlace(holder, c.storage_steps(MIRPlace(holder))), loc=expr.loc, held=holder)
        return c

    def argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """One argument at its binding, at a call and a constructor alike, as
        the slot the callee binds: an Optional holder, an owned leaf lent or
        copied, a view's loan, a record handed over or lent, a container,
        else any admitted scalar expression, evaluated into a temporary
        before the call (a scalar global is read there). Evaluation order
        among arguments is the caller's rule."""
        # THIR converts every argument at its parameter, a base initializer's
        # included, so a borrowed name never reaches a parameter that owns its
        # value unconverted: C++ would not build the owned value from it.
        _require(arg, not (binding.storage and isinstance(arg, th.THIRName) and arg.form is th.Form.BORROW),
                 "argument form disagrees with its passing")
        match binding.cls, binding.storage:
            case MIRClass.OPTIONAL, _:
                return self.optional_operand(arg, binding.value)
            case MIRClass.UNION | MIRClass.TUPLE, _:
                return self.wrapper_argument(arg, binding.value)
            case MIRClass.OWNED_LEAF, _:
                return self.owned_argument(arg, binding.type, binding.passing)
            case MIRClass.VIEW, _:
                # A view passed by value hands the callee the loan it holds.
                return self.holding(self.view_value(arg, binding.type))
            case cls, True if cls in AGGREGATE_CLASSES:
                return self.handed_argument(binding, arg)
            case cls, False if cls in AGGREGATE_CLASSES:
                return self.lent_argument(binding, arg)
            case MIRClass.CONTAINER_VIEW, _:
                return self.view_argument(arg, binding)
        typ = arg.result_type if binding.protocol else binding.type
        plan = self.expr(arg, typ)
        _require(arg, plan.shape_of().type == typ, "call scalar argument mismatch")
        return plan

    def handed_argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """A record or container handed over by value (an `Own[...]`
        parameter, an element a container takes): what aggregate storage
        takes (`aggregate_value`: built, copied, moved or handed over by a
        call) in a temporary of the full expression the callee moves from,
        whose stored loans its value names."""
        # The summary treats the object as private to the callee (what it
        # stores there is no transfer): no holder of the caller's reaches it
        # after the call. Fresh storage of the full expression is reached by
        # nothing else; a moved name's storage is the name's own, and the move
        # is a storage event (MOVE) of it, so a holder of it live after the
        # call is a retention conflict -- MIR's own fact, not sema's move rule
        # (`BUGS.md#own-move-loop-carried-alias`).
        return self.aggregate_operand(arg, binding.type)

    def lent_argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """A record or container lent to a borrowed parameter: the temporary
        THIR bound for it, an aggregate source in place (`aggregate_source`:
        a holder's name, a member, a narrowed Optional's payload, a borrowed
        call result) at its own type or a struct-base ancestor's, never with
        more access than the binding lends, else fresh storage
        (`aggregate_value`) built into a temporary of the full expression and
        lent at the binding's access (never a move: binding the lent
        parameter moves nothing)."""
        ref = binding.value
        if isinstance(arg, th.THIRArgTemp):
            return self.valued(MIRValue(self.argument_temporary(arg, ref).holder, loc=arg.loc))
        if self.named_aggregate(arg) is binding.cls:
            source = self.aggregate_source(arg, binding.cls)
            self.writes.setdefault(arg, False)
            f = self.holding(source)
            held = f.shape_of()
            _require(arg, self.binds_at(arg, held.type, ref.type) and (not held.readonly or ref.readonly),
                     "call record argument mismatch")
            self.lent_loans(arg, held.type, ref.type)
            return f
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRContainerLiteral, th.THIRCopy)) or _user_call(arg),
                 "call needs borrowed record name")
        f = self.aggregate_operand(arg, ref.type)
        g = self.fragment()
        g.value = MIRValue(g.lend(f, ref), loc=arg.loc)
        return g

    def view_argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> Fragment:
        """A container view argument: the region a place lends whole for the
        call (a Span holder lends the region it views)."""
        _require(arg, self.container_receiver(arg), "call container argument needs a place")
        place = self.container_place(arg)
        shape = place.shape_of()
        _require(arg, shape.type == binding.type and (binding.readonly or not shape.readonly),
                 "call container argument mismatch")
        f = self.holding(place, raises=False)
        self.writes[arg] = False
        return f

    def argument_order_rule(self, node: object, operands: tuple[th.THIRExpr, ...]) -> None:
        # Operands evaluate in an unspecified order: one that writes needs every other to be a literal.
        # A call argument admitted without an expression check (a name, a
        # literal, a global handle) records no write fact and writes nothing.
        for operand in operands:
            if self.writes.get(operand, False):
                _require(node, all(other is operand or _literal(other) for other in operands),
                         "order-sensitive eager operands")

    def call_writes(self, expr: Invocation) -> bool:
        """Whether a checked call writes: its summary's parameter writes or
        stored loans, or an argument's own."""
        return (_summary_writes(self.calls[expr])
                or any(self.writes.get(arg, False) for arg in _call_arguments(expr)))

    def owned_argument(self, arg: th.THIRExpr, typ: TpyType, passing: ParamPassing) -> Fragment:
        """An owned-leaf argument: lent for the call at a borrowing passing
        (a name or literal in place, anything built a temporary of the full
        expression), else the callee's own copy, copied or built into a
        temporary the full expression owns."""
        if passing in BORROWING_PASSINGS:
            if view_leaf(arg.result_type):
                # A view of the parameter's family is lent as the view it is.
                plan = self.view_value(arg, arg.result_type)
                _require(arg, view_compatible(arg.result_type, typ), "call owned-leaf argument mismatch")
                return self.holding(plan)
            plan = self.operand(arg, self.owned_value(arg))
            _require(arg, plan.shape_of().type == typ, "call owned-leaf argument mismatch")
            return plan
        plan = self.owned_sink(arg)
        _require(arg, plan.shape_of().type == typ, "call owned-leaf argument mismatch")
        _require(arg, self.mir.active_temporaries is not None, "owned temporary needs full-expression boundary")
        # The callee's own copy: storage of the full expression, handed to the call.
        f = self.fragment()
        value = plan.value
        f.splice(plan)
        f.value = MIRValue(f.materialize(value, arg), loc=arg.loc)
        return f

    def argument_temporary(self, arg: th.THIRArgTemp, reference: MIRShape, *,
                           addr_of: bool = False) -> _Temporary:
        """A record or container built for a call into storage the temporary
        plan places, lent through a holder of it (`reference`, what the
        parameter lends); `addr_of`: its address is what the argument passes
        (an Optional record parameter's pointer)."""
        _require(arg, self.temp_plan is not None, "named argument needs complete temporary plan")
        placement = self.temp_plan.placement(arg)
        _plain(arg, {"init", "cpp_type", "movable"} | ({"addr_of"} if addr_of else set()))
        _require(arg, arg.addr_of is addr_of, "named argument address mismatch")
        _require(arg, arg.form is th.Form.BORROW and (isinstance(arg.init, (th.THIRCtorCall, th.THIRContainerLiteral))
                                                    or _user_call(arg.init))
                 and arg.result_type == reference.type and reference.readonly,
                 "named argument needs readonly record constructor")
        value = self.initializer(arg, lambda: self.aggregate_value(arg.init, reference.type, call=True))
        self.stable_constructor_operands(arg.init)
        _require(arg, not placement.optional or self.movable(reference.type) and arg.would_bank(),
                 "deferred argument needs movable backing")
        temporary = self.placed(arg, placement, reference, value)
        self.argument_temporaries[arg] = temporary
        return temporary

    def placed(self, node: th.THIRArgTemp | th.THIRSlotEmplace, placement: object, holder: MIRShape,
               value: Fragment) -> _Temporary:
        """A record in storage the temporary plan places, lent through a
        holder of it (of shape `holder`); the statement declaring it places
        both slots."""
        shape = _owned_optional(holder.type) if placement.optional else value_shape(holder.type)
        problem = shape_problem(shape, shape.type, producer=True) if placement.optional else None
        _require(node, problem is None, problem)
        storage = self.mir.record(shape, MIRSlotKind.LOCAL, loc=node.loc)
        holder = self.mir.record(holder, loc=node.loc)
        return _Temporary(node, node.loc, placement.index, placement.scope, placement.optional, storage, holder,
                          value)

    def site_backing(self, node: th.THIRStmt, typ: TpyType, *, hoisted: bool = False) -> MIRSlotId:
        """The `std::optional<T>` slot a reseat (its THIR rebind slot,
        `own_slot`) or a hoisted pointer local fills: an owned sum, built
        empty where C++ declares it -- at the statement THIR anchors the
        rebind slot at, or the body's top for a hoisted local -- and filled at
        the site, each loop pass."""
        shape = _owned_optional(typ)
        problem = shape_problem(shape, shape.type, producer=True)
        _require(node, problem is None, problem)
        if hoisted:
            sid = self.mir.record(shape, MIRSlotKind.LOCAL, loc=node.loc)
            self.entry_backings.append(sid)
            return sid
        own = getattr(node, "own_slot", None)
        _require(node, own is not None, "reseat without its rebind slot")
        if own not in self.rebind_slots:
            sid = self.rebind_slots[own] = self.mir.record(shape, MIRSlotKind.LOCAL, loc=node.loc)
            self.unanchored.add(sid)
        sid = self.rebind_slots[own]
        _require(node, self.mir.records[sid].shape == shape, "rebind slot type mismatch")
        return sid

    def anchor_slots(self, stmt: th.THIRStmt) -> Fragment | None:
        """The rebind slots a body-level statement anchors, each built empty
        before it (`StorageSlots.anchor`); the slots of writes MIR does not
        lower as a fill are not MIR's."""
        found = [self.rebind_slots[s] for s in stmt.anchored_slots if s in self.rebind_slots]
        if not found:
            return None
        f = self.fragment()
        for sid in found:
            self.unanchored.discard(sid)
            f.adopt(sid, MIRStorageDuration.BODY)
            # A declaration C++ adds before the statement: no source line of its own.
            f.append(MIRStorageInit(MIRPlace(sid), 0, MIRConstant(None), None))
        return f

    def site_fill(self, expr: th.THIRExpr, typ: NominalType, *, call: bool = False) -> Fragment:
        """The value a fill of an owned sum moves into its payload, built
        into a temporary of its own full expression (`aggregate_temporary`)."""
        return self.full_expression(
            expr, check=lambda: self.aggregate_temporary(expr, self.aggregate_value(expr, typ, call=call)))

    def initializer(self, node: th.THIRArgTemp | th.THIRSlotEmplace, build: Callable[[], Fragment]) -> Fragment:
        """A placed temporary's initializer, built with temporaries of its
        own: its region need, a nested full expression where a statement
        initializes it (`Fragment.initialize`). The enclosing full expression
        owns them too, so its own need is unchanged."""
        outer = self.mir.active_temporaries
        if outer is None:
            return build()
        with self.mir.temporaries() as own:
            value = build()
        outer.extend(own)
        return self.whole(value, node.loc, bool(own))

    def anchored(self, node: object, plan: Fragment) -> Fragment:
        """`plan`, with the temporaries the temporary plan initializes at its node built first."""
        if node in self.anchored_nodes:
            # An emplacement that is also the arm it fills is anchored once.
            return plan
        self.anchored_nodes[node] = True
        found = self.temp_plan.initializations.get(node, ()) if self.temp_plan is not None else ()
        temporaries = tuple(t for t in map(self.temporary_of, (p.node for p in found)) if t is not None)
        if not temporaries:
            return plan
        f = self.fragment()
        f.initialize(temporaries)
        value = plan.value
        f.value = MIRValue(f.splice(plan), value.shape, value.loc)
        return f

    def temporary_of(self, node: object) -> _Temporary | None:
        return self.argument_temporaries.get(node) or self.select_temporaries.get(node)

    def select_temporary(self, expr: th.THIRSlotEmplace, holder: MIRShape) -> _Temporary:
        _require(expr, self.temp_plan is not None, "select storage needs complete temporary plan")
        placement = self.temp_plan.placement(expr)
        _plain(expr, {"value", "cpp_type"})
        _require(expr, placement.optional and placement.initialization is expr,
                 "select storage needs conditional emplacement")
        _require(expr, isinstance(expr.value, (th.THIRCtorCall, th.THIRContainerLiteral)),
                 "select storage needs record constructor")
        value = self.initializer(expr, lambda: self.aggregate_value(expr.value, holder.type))
        self.stable_constructor_operands(expr.value)
        _require(expr, self.movable(holder.type), "select storage needs movable backing")
        temporary = self.placed(expr, placement, holder, value)
        self.select_temporaries[expr] = temporary
        return temporary

    def stable_constructor_operands(self, expr: th.THIRCtorCall | th.THIRContainerLiteral | th.THIRCall
                                    | th.THIRMethodCall) -> None:
        # The temporary is initialized where the plan places it, which may
        # precede other operands: a call building it must write nothing.
        # Narrowed wrapper reads still require a selection proof.
        match expr:
            case th.THIRContainerLiteral():
                operands = (*expr.elements, *expr.values)
            case _:
                operands = _call_arguments(expr)
                _require(expr, not self.call_writes(expr), "named temporary needs stable scalar operands")
        for operand in operands:
            _require(operand, _literal(operand) or isinstance(operand, th.THIRName)
                     and operand.global_binding is None and not self.wraps(operand.name, MIRClass.OPTIONAL)
                     and not self.wraps(operand.name, MIRClass.UNION), "named temporary needs stable scalar operands")

    @contextmanager
    def argument_order(self, expr: th.THIRExpr) -> Iterator[None]:
        before = len(self.argument_temporaries)
        yield
        if len(self.argument_temporaries) != before:
            _require(expr, self.ordered_temporary_expression(expr),
                     "named argument crosses unproven evaluation order")

    def ordered_temporary_expression(self, expr: th.THIRExpr) -> bool:
        match expr:
            case th.THIRName():
                return (expr.global_binding is None and not self.wraps(expr.name, MIRClass.OPTIONAL)
                        and not self.wraps(expr.name, MIRClass.UNION) and not self.payload_alias(expr.name))
            case th.THIRLiteral() | th.THIRCharLiteral() | th.THIRSelf():
                return True
            case th.THIRUnaryArith():
                return self.ordered_temporary_expression(expr.operand)
            case th.THIRCoerce():
                return self.ordered_temporary_expression(expr.expr)
            case th.THIRUnaryNot():
                return self.ordered_temporary_expression(expr.operand)
            case th.THIRArgTemp():
                return expr in self.argument_temporaries
            case th.THIRSlotEmplace():
                return expr in self.select_temporaries
            case th.THIRCall() | th.THIRMethodCall() | th.THIRCtorCall() if (
                    _user_call(expr) or isinstance(expr, th.THIRCtorCall)):
                return (expr in self.calls and not _summary_writes(self.calls[expr])
                        and all(self.ordered_temporary_expression(arg) for arg in _call_arguments(expr)))
            case th.THIRBinOp():
                return all(self.ordered_temporary_expression(arg) for arg in (expr.left, expr.right))
            case th.THIRValueSelect():
                return all(self.ordered_temporary_expression(arg) for arg in (expr.lhs, expr.rhs))
            case th.THIRIfExpr():
                return all(self.ordered_temporary_expression(arg) for arg in (expr.cond, expr.then, expr.orelse))
            case _:
                return False

    def global_binding(self, expr: th.THIRName | th.THIRModuleVar | th.THIRWalrus,
                       *, write: bool = False) -> TpyType:
        fact = expr.global_binding
        owned = isinstance(fact, th.THIRGlobalBinding) and owned_leaf(fact.type)
        _require(expr, isinstance(fact, th.THIRGlobalBinding) and bool(fact.module and fact.name)
                 and leaf_global(fact.type) and (expr.form is th.Form.VALUE or owned)
                 and fact.type == expr.result_type and type(fact.writable) is bool,
                 "missing or invalid scalar global binding")
        # Module init is not lowered, so nothing proves a replaced global's borrowers dead.
        _require(expr, not (write and owned), "owned-leaf global write")
        _require(expr, not write or fact.writable, "global binding is not writable")
        # Code running while the frame is suspended may write any global.
        _require(expr, self.frame is None, "global across a suspension")
        identity = MIRGlobalId(fact.module, fact.name)
        previous = self.globals.get(identity)
        _require(expr, previous is None or previous.type == fact.type, "inconsistent global type")
        if previous is None:
            # An owned-leaf global is read through a readonly handle on its
            # external storage; any other is readonly unless a use writes it
            # (the slot's access, set when assembly freezes it).
            shape = owned_leaf_shape(fact.type, MIRRole.LOAN) if owned else scalar_shape(fact.type)
            self.global_slots[identity] = self.mir.slot(shape, MIRSlotKind.GLOBAL, fact.name, global_id=identity)
        if previous is None or fact.writable:
            self.globals[identity] = fact
        return fact.type

    def parameter_slot(self, p: th.THIRParam, binding: MIRParameterBinding) -> tuple[MIRShape, MIRShape | None]:
        """The slot shape of parameter `p` bound at `binding` -- its value --
        with what the body's layouts need for it registered (`register`),
        and for an `Own[...]` aggregate the holder the body reaches its own
        storage through."""
        shape = binding.value
        self.register(p, shape)
        if shape.cls in AGGREGATE_CLASSES and shape.is_storage:
            # An aggregate handed over at OWN binds the caller's temporary
            # (`R&&`), which ends with the caller's full expression. It
            # is modeled as the body's own storage ending at body exit:
            # an earlier end, so never a missed conflict. Reached through
            # a holder as an owned record local is; never reseated. The
            # loans it stores are the caller's, seeded at entry.
            self.owned_locals.add(p.name)
            self.fixed_owned.add(p.name)
            return shape, holder_form(shape)
        return shape, None

    def register(self, node: object, shape: MIRShape, *, allow_owned: bool = False) -> None:
        """What the body's layouts need for a value of `shape`, its refusals
        raised against `node`: a container's members (`register_container`),
        an owned leaf's opaque layout, a tuple's inline records
        (`tuple_members`, an owning tuple only where `allow_owned`), record
        storage's verified definition. A sum storing a record inline is a
        producer's slot (`_owned_optional`), never a value laid out here."""
        _require(node, not shape.owned_sum, f"unsupported {shape.cls.name.lower()} record holding")
        match shape.cls:
            case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW:
                self.register_container(node, shape.type)
            case MIRClass.OWNED_LEAF:
                self.leaf_layout(node, shape.type)
            case MIRClass.TUPLE:
                self.tuple_members(node, shape, allow_owned=allow_owned)
            case MIRClass.RECORD if shape.is_storage:
                self.record_layout(node, shape.type)

    def bound_parameter(self, slot: MIRSlotId, binding: MIRParameterBinding) -> None:
        """Record the binding a parameter slot was built from, receiver first."""
        assert self.mir.records[slot].shape == binding.value
        self.parameter_bindings.append(binding)

    def check(self) -> None:
        """Check and build the body; `assembly` is what `emit.assemble` closes it with."""
        fn, node, header = self.fn, self.node, self.header
        if self.temp_plan is not None:
            validate_plan(fn.body, self.temp_plan)
            for placement in self.temp_plan.placements:
                _require(placement.node, not (isinstance(placement.node, th.THIRSlotEmplace)
                         and isinstance(placement.declaration, th.THIRWhile)),
                         "repeated condition select emplacement")
        if fn is not None:
            _require(fn, fn.error_return_cpp is None, "error-return body")
            _require(fn, not fn.layout.hoisted_locals, "hoisted declarations")
        callee = fn.resolved_callee if fn is not None else None
        # A follows-receiver result has the access of the body's own receiver.
        receiver_readonly = header.receiver is not None and header.receiver.readonly
        # A body THIR resolved no callee for (an overload) publishes no signature: its declared type's.
        published = callee.signature.return_representation if callee is not None else None
        self.return_representation = published if published is not None else return_representation(self.return_type)
        result = call_result(callee, receiver_readonly, declared=self.return_type,
                             representation=self.return_representation)
        if result is not None and value_class(result.type) in AGGREGATE_CLASSES:
            # A record or container returned by reference: its holder, of the declared type.
            problem = lent_shape_problem(self.return_type, result)
            _require(node, problem is None, problem)
            self.register(node, value_shape(result.type))
            self.result = result
        elif (owned := owned_value_type(self.return_type)) is not None:
            _require(node, self.return_representation is Representation.STORAGE, "unsupported return type")
            self.leaf_layout(node, owned)
        elif view_leaf(self.return_type):
            self.result = result
            _require(node, self.result is not None, "unsupported return type")
        elif region_view(self.return_type):
            # A container view result views a region the caller reaches through the
            # arguments: a borrowed result, summarized by its origins.
            self.register_container(node, unwrap_readonly(self.return_type))
            self.result = result
            _require(node, self.result is not None, "unsupported return type")
        elif (bare := owned_aggregate_result(self.return_type)) is not None:
            # The body's storage returned by value; the caller destroys what
            # it receives: the aggregate's verified definition.
            _require(node, self.return_representation is Representation.STORAGE, "unsupported return type")
            problem = admission_problem(bare, value_class(bare))
            _require(node, problem is None, problem)
            self.register(node, value_shape(bare))
            # A constructor builds its result where its caller receives it: nothing moves.
            _require(node, self.constructor is not None or self.movable(bare),
                     "owned record result needs movable record")
            self.owned_aggregate_result = bare
        else:
            _require(node, not container_view(unwrap_readonly(self.return_type)),
                     "view of container storage")
            _require(node, not holds_borrowing_view(self.return_type), "view return")
            _require(node, storage_leaf(self.return_type, self.return_representation)
                     or isinstance(self.return_type, VoidType), "unsupported return type")
        mir = self.mir
        # A parameter binds at the type its callers bind (`bound_params`). A
        # body without a signature of its types has no summary (which refuses
        # it as a signature mismatch), so it binds at its own; a frame binds
        # the parameters of the factory that builds it.
        bound = (bound_params(fn) or th.effective_params(fn)) if fn is not None else th.effective_params(self.frame)
        if header.receiver is not None and self.constructor is not None:
            # A constructor's `self` is no parameter: a holder of the record it builds.
            bound = bound[1:]
        elif header.receiver is not None:
            param, *bound = bound
            binding = parameter_binding(param)
            if isinstance(binding, str):
                raise MIRUnsupported(node, binding)
            shape, _ = self.parameter_slot(param, binding)
            self.parameters.add("self")
            receiver = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, "self"))
            self.bound_parameter(receiver, binding)
            self.scope["self"] = _Bound(receiver)
        names: dict[str, MIRSlotId] = {}
        for p, param in zip(header.params, bound, strict=True):
            _plain(p, {"name", "type", "passing", "borrowed_record", "optional_layout", "union_layout", "tuple_layout",
                        "native_container"})
            _require(node, p.name not in self.scope, "duplicate binding")
            # A body's parameter carries the facts THIR published for it.
            binding = parameter_binding(param)
            if isinstance(binding, str):
                raise MIRUnsupported(p, binding)
            shape, holder_shape = self.parameter_slot(p, binding)
            if holder_shape is not None:
                # The caller materialized the aggregate before entry; the body
                # owns it and reaches it through a holder borrowed at entry.
                storage = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, p.name),
                                    MIRStorageDuration.BODY)
                self.bound_parameter(storage, binding)
                holder = mir.slot(holder_shape, MIRSlotKind.LOCAL)
                mir.write(holder, MIRBorrow(MIRPlace(storage)), None)
                self.scope[p.name] = _Bound(holder, storage)
                names[p.name] = storage
                continue
            # Owned storage lives for the body; a scalar wrapper lent by reference is the caller's.
            sid = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, p.name),
                            MIRStorageDuration.BODY if shape.is_storage
                            else MIRStorageDuration.CALLER
                            if caller_storage(shape, passing_representation(binding.passing))
                            else None)
            self.bound_parameter(sid, binding)
            self.scope[p.name] = _Bound(sid)
            names[p.name] = sid
        plans: list[Fragment] = []
        if self.constructor is not None:
            # After the parameter holders: none points into storage the
            # member-init list already moved from.
            self.constructor_result = self.constructor_entry()
            plans = self.constructor_initialization()
        if fn is not None:
            plans += self.declarations(fn.body, 0, top=True)
        else:
            plans.append(self.frame_body())
        _require(node, not self.unanchored, "rebind slot without anchor")
        for backing in self.entry_backings:
            mir.adopt(backing, MIRStorageDuration.BODY)
            mir.append(MIRStorageInit(MIRPlace(backing), 0, MIRConstant(None), None))
        self.sequence(mir, plans)
        if self.constructor_result is not None and mir.current is not None:
            # Falling off the end returns the record built.
            mir.end(MIRReturn(self.constructor_result, None))
        if self.planned:
            _require(fn, len(self.argument_temporaries) + len(self.select_temporaries)
                     == len(self.temp_plan.placements), "temporary plan contains unsupported record storage")
        # Each callee once, as the body's summary table holds them.
        callees = tuple({s.callee.identity: s for s in self.calls.values()}.values())
        for identity, fact in self.globals.items():
            mir.records[self.global_slots[identity]].writable = fact.writable
        self.close_layouts()
        self.assembly = dict(
            void=isinstance(self.return_type, VoidType), return_type=self.return_type,
            kind=(MIRBodyKind.CONSTRUCTOR if self.constructor is not None
                  else function_body_kind(fn if fn is not None else self.frame)),
            callees=callees, result=self.result, layouts=self.layouts(),
            parameter_bindings=tuple(self.parameter_bindings),
            return_representation=self.return_representation, source_name=self.source_name())

    def source_name(self) -> str:
        """The callable's Python name, `Owner.name` for a method, the owner
        by the name its user wrote (an enum's methods live on a companion
        record); a frame's owner is its receiver's record."""
        fn, header = self.fn, self.header
        owner = (fn.resolved_callee.identity.owner if fn is not None and fn.resolved_callee is not None
                 else header.receiver.type.name if fn is None and header.receiver is not None else None)
        if owner is None or "." in header.name:
            return header.name
        spelled = (owner_display_name(header.receiver.type) if header.receiver is not None
                   else owner.rsplit('.', 1)[-1])
        return f"{spelled}.{header.name}"

    def constructor_entry(self) -> MIRSlotId:
        """The record a constructor builds: storage of the body begun at
        entry, its fields not yet initialized, built where its caller
        receives it (`MIRSlot.destination`); and `self`, a holder bound to
        it before any statement and never rebound (`MIRSlot.bound_storage`)."""
        mir = self.mir
        shape = value_shape(self.constructor.layout.type)
        result = mir.adopt(mir.record(shape, MIRSlotKind.TEMPORARY, destination=True), MIRStorageDuration.BODY)
        mir.append(MIRStorageInit(MIRPlace(result), None, None, None))
        holder = mir.adopt(mir.record(holder_form(shape), MIRSlotKind.LOCAL, "self", bound_storage=result))
        mir.write(holder, MIRBorrow(MIRPlace(result)), None)
        self.scope["self"] = _Bound(holder)
        # Never rebound: a write of the name is refused as a parameter's is.
        self.parameters.add("self")
        return result

    def constructor_initialization(self) -> list[Fragment]:
        """The member-init list as the statements C++ runs before the body,
        each the initialization of its field: every ancestor field from its
        base's initializer, then the record's own fields in layout order,
        each an initializing write of its field (`field_write`)."""
        plans = [p for bi, base in self.constructor.bases if (p := self.base_initialization(bi, base)) is not None]
        record = self.constructor.layout.type
        for mil in self.constructor.members:
            # The entry has no THIR node of its own with a location: its value answers for it.
            loc = mil.value.loc
            shape, place = self.member(mil.value, self.binding_place("self", loc), mil.field_identity, record,
                                       "self", MIRStorageWriteMode.INITIALIZE, loc)
            plans.append(self.field_write(mil.value, loc, shape, place, mil.value, MIRStorageWriteMode.INITIALIZE))
        return plans

    def base_initialization(self, bi: th.THIRBaseInit, base: MIRConstructorDefinition) -> Fragment | None:
        """A base initializer: a call of the base's constructor whose result
        initializes the base's fields of the record being built, through
        `self`. A base the source never calls (`args=()`) is C++'s
        `Base()`: a call of a constructor that binds with no argument (a
        defaulted one is not bound here, so the call is refused as any
        call omitting a default is), else built empty, which only a base
        with no fields is."""
        if not bi.args and not base.zero_argument:
            _require(bi, not base.layout.fields, "base constructor not called")
            return None
        loc = _invocation_loc(bi)
        with self.mir.temporaries() as own:
            built = self.call(bi)
        value = self.whole(built, loc, bool(own))
        f = self.fragment()
        with f.full_expression(value):
            f.write(MIRPlace(self.slot_of("self"), (MIRDeref(),)), f.splice(value), loc,
                    MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
        return f

    # --- a generator's frame -------------------------------------------------------

    def frame_body(self) -> Fragment:
        """A generator's body, read off the frame its emission published:
        each parameter a by-value capture holds copied into the frame, the
        frame's locals declared at entry and unassigned until written
        (`hoisted_binding`), then each block the entry reaches -- its leaves
        (`declarations`) closed by its terminator. The body ends without a
        value where it returns or falls off its end."""
        leaves = self.frame
        frame, node = leaves.frame, self.node
        _require(node, not frame.nested_defs, "nested def")
        _require(node, not frame.arg_lifts, "argument held across a suspension")
        _require(node, leaves.yield_hosts_temps, "reference yield")
        reached: set[int] = set()
        pending = [frame.entry_block]
        while pending:
            bid = pending.pop()
            if bid not in reached:
                reached.add(bid)
                pending.extend(terminator_targets(frame.blocks[bid].terminator))
        order = sorted(reached)
        statements: list[th.THIRStmt] = []
        for bid in order:
            block = frame.blocks[bid]
            for region in block.region_stack:
                raise MIRUnsupported(node, _region_feature(region))
            for s in block.stmts:
                _require(node, not isinstance(s, THIRForIterSetup), "loop across a suspension")
                _require(node, not isinstance(s, THIRFinallyExit), "try across a suspension")
                _require(node, isinstance(s, THIRLeafSlot), "with across a suspension")
                leaf = leaves.leaves.get(s)
                _require(node, isinstance(leaf, th.THIRStmt), "missing frame leaf")
                statements.extend(leaf.stmts if isinstance(leaf, th.THIRStmtSeq) else (leaf,))
            _require(node, not isinstance(block.terminator, THIRForAdvance), "loop across a suspension")
            _require(node, not isinstance(block.terminator, THIRMatchDispatch), "match across a suspension")
        loc = node.loc
        f = self.fragment()
        for p, capture in zip(self.header.params, frame.captures, strict=True):
            _require(node, capture in (FrameCaptureKind.REF, FrameCaptureKind.VALUE, FrameCaptureKind.OWNED_COPY),
                     f"unsupported {capture.name.lower()} capture")
            shape = self.bound(p.name)
            if capture is FrameCaptureKind.REF or shape.is_scalar or shape.is_storage:
                continue
            # The frame's own copy of a lent parameter: the parameter keeps
            # the loan its caller lends, as a body copying a reassigned one.
            _require(node, self.owned_leaf_bound(p.name) is False, "by-value capture of a borrowed parameter")
            parameter = self.name_place(p.name, loc)
            copy = value_shape(shape.type)
            f.splice(self.put_param_copy(loc, self.bind(p.name, copy, loc=loc).slot, parameter.sink(copy)))
        declared = _leaf_declarations(tuple(statements))
        for local in frame.locals:
            if local.name in declared:
                continue
            _require(node, local.type is not None, "untyped frame local")
            self.place_hoists(f, [self.hoisted_binding(node, local.name, local.type, local)], loc)
        blocks = {bid: f.block() for bid in order}
        f.end(MIRGoto(blocks[frame.entry_block].id, loc))
        for bid in order:
            block = frame.blocks[bid]
            f.current = blocks[bid]
            for s in block.stmts:
                leaf = leaves.leaves[s]
                self.sequence(f, self.declarations(leaf.stmts if isinstance(leaf, th.THIRStmtSeq) else (leaf,), 0,
                                                   top=True))
            if f.current is None:
                continue
            t = block.terminator
            match t:
                case THIRFall(next_bb=target):
                    f.end(MIRGoto(blocks[target].id, None))
                case THIRBranch():
                    cond = leaves.leaves.get(t.cond)
                    _require(node, isinstance(cond, th.THIRExpr), "missing frame leaf")
                    condition = self.full_expression(cond)
                    _require(cond, condition.shape_of().type == BOOL, "condition requires bool")
                    f.branch(f.full_expression_value(condition), blocks[t.then_bb].id, blocks[t.else_bb].id,
                             cond.loc)
                case THIRSuspend():
                    self.suspension(f, t, blocks[t.resume_bb].id)
                case THIRFrameReturn():
                    leaf = leaves.leaves.get(t.slot)
                    _require(node, isinstance(leaf, th.THIRResumableReturn), "missing frame leaf")
                    f.splice(self.stmt(leaf, 0))
                case THIRFallOff():
                    f.end(MIRReturn(None, None))
                case _:
                    raise MIRUnsupported(node, f"unsupported frame terminator {type(t).__name__}")
        return f

    def suspension(self, f: Fragment, t: THIRSuspend, resume: MIRBlockId) -> None:
        """A `yield`: its value built in a scope of its own -- an inert leaf,
        or owned storage the yield fills -- read by the suspension, which
        resumes at `resume`."""
        payload = t.payload
        _require(self.node, isinstance(payload, THIRYieldSuspension), "async body")
        expr = self.frame.leaves.get(payload.value) if payload.value is not None else None
        _require(self.node, isinstance(expr, th.THIRExpr), "missing frame leaf")
        typ = self.frame.frame.func.generator_yield_type
        loc = payload.source_loc
        entry = f.block()
        f.end(MIRGoto(entry.id, loc))
        f.current = entry
        with f.scope():
            if storage_leaf(typ):
                value = self.full_expression(expr, expected=typ)
                _require(expr, value.shape_of().type == typ, "yield type mismatch")
                yielded = f.full_expression_value(value)
            else:
                owned = owned_value_type(typ)
                _require(expr, owned is not None, "reference yield")
                self.leaf_layout(expr, owned)
                value = self.full_expression(expr, check=lambda: self.owned_sink(expr))
                _require(expr, value.shape_of().type == owned, "yield type mismatch")
                yielded = f.slot(value_shape(owned), storage_duration=f.scoped_duration())
                with f.full_expression(value):
                    f.write(yielded, f.splice(value), loc, MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
            f.append(MIRSuspend(yielded, loc))
            f.end(MIRGoto(resume, loc))

    # --- statements ---------------------------------------------------------------

    def sequence(self, f: Fragment, statements: list[Fragment]) -> None:
        """A statement list run where `f` is open: what follows a statement
        with no open exit is not built, though it is checked."""
        for stmt in statements:
            if f.current is None:
                break
            f.splice(stmt)

    def scoped_into(self, f: Fragment, statements: list[Fragment]) -> None:
        with f.scope():
            self.sequence(f, statements)

    def duration_of(self, f: Fragment, placement: bool | None) -> object:
        """The storage duration a statement's placement gives: the body's
        (True), the statement's scope's (False), none (None)."""
        if placement is None:
            return None
        return MIRStorageDuration.BODY if placement else f.scoped_duration()

    def declared_storage(self, f: Fragment, placement: bool | None, missing: object | None) -> object:
        """The storage duration of what a declaration owns; a declaration
        THIR placed no storage for is refused where the body reaches it."""
        if missing is not None:
            f.refuse(missing, "missing direct storage placement")
        return self.duration_of(f, placement)

    def place_hoists(self, f: Fragment, hoists: list[tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None]],
                     loc: object) -> None:
        """Bindings declared before the statement that assigns them: an
        Optional record's backing starts empty, a scalar wrapper at its
        physical default."""
        for slot, backing, default in hoists:
            if backing is not None:
                storage = f.adopt(backing, f.scoped_duration())
                f.append(MIRStorageInit(MIRPlace(storage), 0, MIRConstant(None), loc))
                f.adopt(slot)
                continue
            dest = f.adopt(slot, f.scoped_duration() if default is not None else None)
            if default is not None:
                alternative, value = default
                f.append(MIRStorageInit(MIRPlace(dest), alternative, MIRConstant(value), loc))

    def put_declare(self, loc: object, slot: MIRSlotId, value: Fragment | None, placement: bool | None) -> Fragment:
        """A local and its initializer, written from the value's own form."""
        f = self.fragment()
        dest = f.adopt(slot, self.duration_of(f, placement))
        if value is not None:
            fact = MIRStorageWrite(MIRStorageWriteMode.INITIALIZE) if placement is not None else None
            with f.full_expression(value):
                f.write(dest, f.splice(value), loc, fact)
        return f

    def put_narrow_alias(self, loc: object, slot: MIRSlotId, source: MIRPlace) -> Fragment:
        f = self.fragment()
        f.write(f.adopt(slot), MIRUnionExtract(source), loc)
        return f

    def put_union_declare(self, loc: object, slot: MIRSlotId, value: Fragment, placement: bool | None,
                          missing: object | None) -> Fragment:
        f = self.fragment()
        dest = f.adopt(slot, self.declared_storage(f, placement, missing))
        f.write(dest, f.splice(value), loc,
                f.payload_write(self.mir.records[dest].shape.scalar_wrapper, MIRStorageWriteMode.INITIALIZE))
        return f

    def put_union_assign(self, loc: object, slot: MIRSlotId, value: Fragment, inline_payload: bool) -> Fragment:
        f = self.fragment()
        f.write(slot, f.splice(value), loc, f.payload_write(inline_payload, MIRStorageWriteMode.IN_PLACE))
        return f

    def record_backing(self, f: Fragment, backing: tuple[object, MIRShape, MIRShape], value: Fragment,
                       write: object, duration: object) -> MIRWrap:
        """An Optional's payload built into its own record storage, lent through a holder."""
        loc, storage_shape, holder_shape = backing
        storage = f.slot(storage_shape, storage_duration=duration)
        f.expression_span(storage, value)
        f.write(storage, f.splice(value), loc, write)
        reference = f.holder(holder_shape, MIRBorrow(MIRPlace(storage)), loc)
        return MIRWrap(1, MIRPlace(reference))

    def put_optional_declare(self, loc: object, slot: MIRSlotId, value: Fragment, placement: bool | None,
                             backing: tuple[object, MIRShape, MIRShape] | None, operation: object | None,
                             missing: object | None) -> Fragment:
        f = self.fragment()
        dest = f.adopt(slot, self.declared_storage(f, placement, missing))
        written = (self.record_backing(f, backing, value, MIRStorageWrite(MIRStorageWriteMode.INITIALIZE),
                                       f.scoped_duration())
                   if backing is not None else f.splice(value))
        if operation is not None:
            # The holder points into the field: a loan replacing the field ends.
            f.borrow_operation(operation)
        f.write(dest, written, loc,
                f.payload_write(self.mir.records[dest].shape.scalar_wrapper, MIRStorageWriteMode.INITIALIZE))
        return f

    def put_optional_assign(self, loc: object, slot: MIRSlotId, value: Fragment,
                            backing: MIRSlotId | None, in_place: bool,
                            inline_payload: bool) -> Fragment:
        """An Optional local's value replaced: from its source, or a record
        built into its site's owned sum (lent by `optional_to_ptr`, a sum
        copy) or into its payload in place."""
        f = self.fragment()
        if backing is not None:
            self.fill_site(f, loc, slot, backing, value)
        elif in_place:
            f.write(MIRPlace(slot, sole_object(self.mir.records[slot].shape)), f.splice(value), loc,
                    MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, slot))
        else:
            f.write(slot, f.splice(value), loc, f.payload_write(inline_payload, MIRStorageWriteMode.IN_PLACE))
        return f

    def put_owned_declare(self, loc: object, slot: MIRSlotId, storage: MIRSlotId, value: Fragment,
                          placement: bool | None, hoisted: bool, missing: object | None) -> Fragment:
        """A local owning its record or container storage, reached through its holder;
        storage of a hoisted backing is initialized at its own site."""
        f = self.fragment()
        if hoisted:
            # The site's owned sum, built empty at the body's top, filled here.
            self.fill_site(f, loc, f.adopt(slot), storage, value)
            return f
        f.adopt(storage, self.declared_storage(f, placement, missing))
        f.expression_span(storage, value)
        with f.full_expression(value):
            f.write(storage, f.splice(value), loc, MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
        f.write(f.adopt(slot), MIRBorrow(MIRPlace(storage)), loc)
        return f

    def put_replacement(self, loc: object, slot: MIRSlotId, value: Fragment, storage: MIRSlotId | None) -> Fragment:
        """A record holder's referent replaced: built into new storage
        (`storage`) or into the referent in place."""
        f = self.fragment()
        if storage is not None:
            self.fill_site(f, loc, slot, storage, value)
            return f
        with f.full_expression(value):
            f.write(MIRPlace(slot, sole_object(self.mir.records[slot].shape)), f.splice(value), loc,
                    MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, slot))
        return f

    def fill_site(self, f: Fragment, loc: object, slot: MIRSlotId, storage: MIRSlotId, value: Fragment) -> None:
        """A site's owned sum filled from the record its value built
        (`site_fill`), and the holder rebound by its shape: a record holder
        borrows the payload, an Optional holder takes `optional_to_ptr` of
        the sum (a `wrapper_copy`). The holder is rebound within the full
        expression, so the line rebinds it where it fills the sum."""
        holder = self.mir.records[slot].shape
        f.expression_span(storage, value)
        with f.full_expression(value):
            lent = f.fill_sum(storage, f.splice(value), loc)
            f.write(slot, self.wrapper_copy(MIRPlace(storage), holder) if holder.cls is MIRClass.OPTIONAL
                    else MIRBorrow(lent), loc)

    def put_borrow_binding(self, loc: object, slot: MIRSlotId, declare: bool, source: Fragment, deref: bool,
                           operation: object) -> Fragment:
        """A holder bound or rebound to a borrow of a place (`deref`: through
        a tuple member's pointer, which a parameter captures)."""
        f = self.fragment()
        place = f.splice(source)
        value = MIRBorrow(MIRPlace(place.root, (*place.projections, MIRDeref())) if deref else place)
        if declare:
            f.adopt(slot)
        f.borrow_operation(operation)
        f.write(slot, value, loc)
        return f

    def put_borrowed_binding(self, loc: object, slot: MIRSlotId, declare: bool, value: Fragment,
                             operation: object) -> Fragment:
        """A holder bound or rebound to a borrowed result: a call's, a
        conditional's, or another holder's referent."""
        f = self.fragment()
        source = f.splice(value)
        if declare:
            f.adopt(slot)
        f.borrow_operation(operation)
        f.write(slot, MIRAlias(source), loc)
        return f

    def put_assign(self, loc: object, target: Fragment, value: Fragment, mode: MIRStorageWriteMode | None) -> Fragment:
        """A place written from a value of its own form (`mode`: how the
        storage it holds is written -- initialized or replaced in place; None
        for a place holding none); the place is evaluated first."""
        f = self.fragment()
        place = f.splice(target)
        fact = MIRStorageWrite(mode) if mode is not None else None
        with f.full_expression(value):
            f.write(place, f.splice(value), loc, fact)
        return f

    def put_scalar_assign(self, loc: object, target: Fragment, value: Fragment) -> Fragment:
        """A scalar written to a place evaluated within the full expression."""
        f = self.fragment()
        with f.full_expression(value):
            place = f.splice(target)
            f.write(place, MIRRead(MIRPlace(f.splice(value))), loc)
        return f

    def put_view_member_assign(self, loc: object, target: Fragment, value: Fragment) -> Fragment:
        """A view member rebound to the loan a view holder holds."""
        f = self.fragment()
        place = f.splice(target)
        with f.full_expression(value):
            f.write(place, MIRAlias(f.splice(value)), loc)
        return f

    def put_expression(self, loc: object, value: Fragment) -> Fragment:
        """A full expression evaluated for its effect."""
        f = self.fragment()
        with f.full_expression(value):
            f.splice(value)
        return f

    def put_param_copy(self, loc: object, slot: MIRSlotId, value: Fragment) -> Fragment:
        """The body's owned copy of a borrowed owned-leaf parameter it reassigns (`value`: the parameter sunk)."""
        f = self.fragment()
        local = f.adopt(slot, f.scoped_duration())
        f.write(local, f.splice(value), loc, MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
        return f

    def put_append(self, loc: object, target: Fragment, typ: TpyType, value: Fragment) -> Fragment:
        """`t += v` in place: it reads the old value and v, then replaces t."""
        f = self.fragment()
        place = f.splice(target)
        with f.full_expression(value):
            operand = f.splice(value)
            current = f.holder(owned_leaf_shape(typ, MIRRole.LOAN), MIRBorrow(place), loc)
            f.write(place, MIROp("+=", (current, operand), may_raise=True), loc,
                    MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))
        return f

    def put_jump(self, loc: object, head: bool) -> Fragment:
        """`continue` (`head`) or `break`."""
        f = self.fragment()
        f.end(MIRGoto(self.loops[-1][0 if head else 1], loc))
        return f

    def declarations(self, stmts: tuple[th.THIRStmt, ...], loops: int, *, top: bool = False) -> list[Fragment]:
        """The fragments of a statement list: each statement's, after the
        temporaries it declares and initializes (an if or a while carries
        its own), and at the body's level after the rebind slots it anchors."""
        plans: list[Fragment] = []
        for stmt in stmts:
            if isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl)):
                plan = self.declaration(stmt, loops)
            else:
                plan = self.stmt(stmt, loops)
            if top and (anchored := self.anchor_slots(stmt)) is not None:
                plans.append(anchored)
            if not isinstance(stmt, (th.THIRIf, th.THIRWhile)):
                temporaries = self.statement_temporaries(stmt)
                if temporaries is not None:
                    plans.append(temporaries)
            if plan is not None:
                plans.append(plan)
        return plans

    def statement_temporaries(self, stmt: th.THIRStmt) -> Fragment | None:
        """The storage and holders of the temporaries the temporary plan
        declares at a statement in the scope it runs in, then those it
        initializes there."""
        plan = self.temp_plan
        if not self.planned or (stmt not in plan.declarations and stmt not in plan.initializations):
            return None
        f = self.fragment()
        for temporary in map(self.temporary_of, (p.node for p in plan.declarations.get(stmt, ()))):
            if temporary is None or temporary.scope != self.temp_now:
                continue
            f.adopt(temporary.storage, f.scoped_duration())
            f.publish(temporary.key, MIRPlace(temporary.storage, (MIRUnionPayload(1),) if temporary.optional else ()))
            f.adopt(temporary.holder)
            if temporary.optional:
                f.append(MIRStorageInit(MIRPlace(temporary.storage), 0, MIRConstant(None), temporary.loc))
        f.initialize(tuple(t for t in map(self.temporary_of, (p.node for p in plan.initializations.get(stmt, ())))
                           if t is not None), nested=True)
        return f

    @contextmanager
    def temp_in(self, scope: int | None) -> Iterator[None]:
        """The statements checked here run in temporary plan scope `scope`
        (none opened: the enclosing one)."""
        outer = self.temp_now
        if self.planned and scope is not None:
            self.temp_now = scope
        try:
            yield
        finally:
            self.temp_now = outer

    def temp_scope(self, owner: th.THIRStmt, role: str) -> int | None:
        """The temporary plan scope a scope of the body opens for `owner`."""
        return self.temp_plan.scope(owner, role) if self.temp_plan is not None else None

    def placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> bool | None:
        """Where a wrapper declaration's own storage lives: its scope, or no
        placement THIR gave (None). An aggregate's is MIR's own
        (`owned_placement`)."""
        return False if stmt.storage_placement is th.THIRStoragePlacement.SCOPE else None

    def declare(self, loc: object, bound: _Bound, value: Fragment | None) -> Fragment:
        """A local's declaration: storage the binding owns is the scope's."""
        return self.put_declare(loc, bound.slot, value,
                                False if self.mir.records[bound.slot].shape.is_storage else None)

    def missing_placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, needs: bool) -> th.THIRStmt | None:
        """The declaration, when its storage needs a placement THIR did not
        give: refused only if the body reaches it (`Fragment.refuse`)."""
        return stmt if needs and self.placement(stmt) is None else None

    def declaration(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, loops: int) -> object:
        _require(stmt, stmt.name not in self.scope, "duplicate binding")
        if stmt.storage_placement is not None:
            _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.SCOPE or (
                isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                and stmt.owned_storage is not None and stmt.storage_placement is th.THIRStoragePlacement.BODY),
                     "hoisted initial backing")
        # Storage a declaration in a loop owns is built per pass: in its
        # scope, or the body's where THIR hoists it.
        if (loops and _aggregate_class(stmt.resolved_type) is not None and layout_fact(stmt) is None
                and self.owns(stmt)):
            _require(stmt, isinstance(stmt.init, th.THIRCtorCall) or self.owned_placement(stmt) is not None,
                     "loop copy or move needs scoped or hoisted storage")
        if isinstance(stmt, th.THIRVarDecl) and stmt.tuple_storage_alias is not None:
            return self.tuple_alias(stmt)
        # A record or container local is classed by its declared type; a
        # wrapper's per-member forms are its layout fact's.
        cls = _aggregate_class(stmt.resolved_type)
        if cls is not None and layout_fact(stmt) is None:
            return self.aggregate_local(stmt, cls)
        _require(stmt, stmt.union_layout is None or isinstance(stmt, th.THIRVarDecl) and stmt.init is not None,
                 "unsupported union declaration")
        # A tuple, union or Optional declaration: the class of the value its one layout fact lays out.
        shape = self.laid_out(stmt, stmt.resolved_type, allow_owned=True)
        if shape is not None and shape.cls is MIRClass.UNION:
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "union_layout",
                          "cpp_local_representation", "union_literal", "storage_placement"})
            # A scalar payload lives in storage of the declaration's scope; a record one is pointed at.
            scalar = shape.scalar_wrapper
            _require(stmt, stmt.form is (th.Form.VALUE if scalar else th.Form.BORROW)
                     and (not scalar or stmt.cpp_local_representation is None),
                     "union declaration representation mismatch")
            value = self.union_source(stmt.init, shape, stmt.union_literal, stmt.union_layout)
            bound = self.bind(stmt.name, shape, loc=stmt.loc)
            return self.put_union_declare(stmt.loc, bound.slot, value, self.placement(stmt) if scalar else None,
                                 self.missing_placement(stmt, scalar))
        if shape is not None and shape.cls is MIRClass.OPTIONAL:
            return self.optional_declaration(stmt, shape)
        if isinstance(stmt, th.THIRVarDecl) and isinstance(stmt.resolved_type, TupleType):
            _require(stmt, shape is not None and shape.cls is MIRClass.TUPLE, "missing or invalid layout fact")
            owning = shape.owned_tuple
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "tuple_layout"}
                   | ({"storage_placement"} if owning else set()))
            _require(stmt, stmt.form in ((th.Form.STORAGE,) if owning else (th.Form.VALUE, th.Form.BORROW))
                     and stmt.init is not None, "unsupported tuple declaration")
            members = shape.members
            if owning:
                _require(stmt, isinstance(stmt.init, th.THIRTupleLiteral)
                         and stmt.init.tuple_layout == stmt.tuple_layout
                         and stmt.storage_placement is th.THIRStoragePlacement.SCOPE,
                         "owned tuple needs direct constructor literal")
            # An owned tuple's record members are built into temporaries of
            # its full expression, which the tuple moves from.
            value = (self.full_expression(stmt.init, check=lambda: self.tuple_expr(stmt.init, allow_owned=True))
                     if owning else self.tuple_expr(stmt.init))
            self.tuple_compatible(stmt, value.shape_of().members, members)
            bound = self.bind(stmt.name, shape, loc=stmt.loc)
            if owning and id(stmt) in self.body_declarations:
                self.tuple_roots[stmt.name] = stmt.name
            if not owning:
                return self.declare(stmt.loc, bound, self.tuple_copy(value, stmt.name))
            return self.put_declare(stmt.loc, bound.slot, value, False)
        _require(stmt, isinstance(stmt, th.THIRVarDecl), "missing alias binding")
        # A narrowed Optional field's payload lives inline in the field,
        # which MIR keeps whole: a binding of the payload alone is not modeled.
        _require(stmt, not (isinstance(stmt.init, th.THIRFieldAccess) and stmt.init.narrowed_deref),
                 "borrow of an Optional field's payload")
        # A VALUE-form leaf is held by value whichever way its C++
        # declaration is spelled (an enum local names its type).
        _plain(stmt, {"name", "resolved_type", "init", "is_const", "cpp_type"})
        if owned_leaf(stmt.resolved_type):
            return self.owned_local(stmt)
        if view_leaf(stmt.resolved_type):
            return self.view_local(stmt)
        if region_view(stmt.resolved_type):
            return self.span_local(stmt)
        _require(stmt, not container_view(unwrap_readonly(stmt.resolved_type)),
                 "view of container storage")
        _require(stmt, not holds_borrowing_view(stmt.resolved_type), "view local")
        _require(stmt, stmt.form is th.Form.VALUE and storage_leaf(stmt.resolved_type),
                 "unsupported local type or form")
        _require(stmt, stmt.name not in self.scope, "duplicate binding")
        value = None
        if stmt.init is not None:
            value = self.full_expression(stmt.init, expected=stmt.resolved_type)
            _require(stmt, value.shape_of().type == stmt.resolved_type, "initializer type mismatch")
            value.sink(value.shape_of())
        return self.declare(stmt.loc, self.bind(stmt.name, scalar_shape(stmt.resolved_type), loc=stmt.loc), value)

    def owns(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> bool:
        """Whether a record or container declaration binds storage of its
        own: a local in a value or storage form, a BORROW local whose binding
        THIR builds over a rebind slot (`BindingConstruct.REBIND_SLOT`: C++
        lifetime-extends the fresh value it binds), a pointer-slot
        declaration over an rvalue. A reseated alias has a rebind slot too,
        but THIR constructs it as a pointer."""
        if isinstance(stmt, th.THIRVarDecl):
            return stmt.form in (th.Form.VALUE, th.Form.STORAGE) or (
                stmt.form is th.Form.BORROW and isinstance(stmt.binding, BindingRepr)
                and stmt.binding.construct is BindingConstruct.REBIND_SLOT)
        return stmt.kind in (th.PtrSlotKind.RECORD_RVALUE, th.PtrSlotKind.RECORD_HOISTED)

    def owned_placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> bool | None:
        """Where storage a declaration owns lives: the body where THIR
        hoists the binding -- its declaration kind (`RECORD_HOISTED`) and
        its binding's construction (`RECORD_HOISTED_SLOT`) say so --, else
        its declaration's scope; None where those inputs disagree."""
        kind = isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
        binding = stmt.binding if isinstance(stmt.binding, BindingRepr) else None
        if binding is not None and (binding.construct is BindingConstruct.RECORD_HOISTED_SLOT) is not kind:
            return None
        return kind

    def aggregate_local(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, cls: MIRClass) -> Fragment:
        """A record or container local: what it holds is its declared type
        (admitted by `admission_problem`), its access its declaration's
        (`_aggregate_holder`); whether it owns its storage or holds another
        place is THIR's decision of which C++ slot it is."""
        problem = admission_problem(native_container_subject(stmt.resolved_type), cls)
        _require(stmt, problem is None, problem)
        var = isinstance(stmt, th.THIRVarDecl)
        if self.owns(stmt):
            return self.owned(stmt)
        _require(stmt, stmt.form is th.Form.BORROW if var else stmt.kind is th.PtrSlotKind.PTR_ADDR,
                 "unsupported local type or form")
        return self.alias_local(stmt, declaration=True)

    def rebinds_holder(self, stmt: th.THIRAssign | th.THIRPtrLocalRebind) -> bool:
        """Whether `stmt` points a record or container holder at another
        place: a pointer local's rebind, or an assignment to a bare holder
        name whose binding THIR made a pointer (`BindingRepr.pointer`). An
        assignment through the pointer, or to a reference holder (`T&`,
        which has no pointer to reseat), writes the referent: no reseat."""
        if isinstance(stmt, th.THIRPtrLocalRebind):
            return stmt.kind is th.PtrSlotKind.PTR_ADDR and self.holder(stmt.name) is not None
        target = stmt.target
        if isinstance(target, th.THIRSelf):
            name = "self"
        elif isinstance(target, th.THIRName) and target.global_binding is None:
            name = target.name
        else:
            return False
        if self.holder(name) is None:
            return False
        _require(stmt, not getattr(target, "deref", False), "assignment through a holder")
        # THIR's pointer verdict on the reseat: only a `T*` slot repoints.
        _require(stmt, isinstance(stmt.binding, BindingRepr) and stmt.binding.pointer,
                 "assignment to a reference holder")
        return True

    def alias_local(self, stmt: th.THIRStmt, *, declaration: bool = False) -> Fragment:
        """A record or container holder bound or rebound to a place or a
        borrowed result, one path chosen by the source: a name by its
        holding (a holder's referent aliased, owned storage borrowed in
        place), a field or a parameter's tuple member (borrowed in place),
        a container element (`element_binding`), a call's or a
        conditional's borrowed result."""
        facts = {"alias_binding", "storage_borrow", "native_container"}
        if declaration:
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation"
                          if isinstance(stmt, th.THIRVarDecl) else "kind"} | facts)
            _require(stmt, stmt.name not in self.scope, "duplicate binding")
            name, source, holder = stmt.name, stmt.init, _aggregate_holder(stmt.resolved_type, _declared_const(stmt))
            self.register(stmt, holder)
        else:
            if isinstance(stmt, th.THIRAssign):
                _plain(stmt, {"target", "value"} | facts)
                name = self.reference_name(stmt.target)
            else:
                _plain(stmt, {"name", "kind", "value"} | facts)
                name = stmt.name
            source, holder = stmt.value, self.bound(name)
            _require(stmt, name not in self.parameters, "reference parameter reseat")
            _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")
        _require(stmt, source is not None, "missing alias source")
        if isinstance(source, th.THIRFormConvert):
            _plain(source, {"value", "is_const"})
            _require(source, source.form is th.Form.BORROW and source.is_const == holder.readonly
                     and native_container_subject(source.result_type) == holder.type,
                     "unsupported alias conversion")
            source = source.value

        def bound_slot() -> MIRSlotId:
            return self.bind(name, holder, loc=stmt.loc).slot if declaration else self.slot_of(name)
        match source:
            case th.THIRName() | th.THIRSelf() if getattr(source, "global_binding", None) is None:
                # Every aggregate name is a holder: the new holder aliases its referent.
                source_name = self.reference_name(source)
                held = self.bound(source_name)
                _require(stmt, held.cls is holder.cls and held.type == holder.type, "alias source mismatch")
                _require(stmt, not held.readonly or holder.readonly, "alias increases access")
                value = self.valued(MIRValue(self.slot_of(source_name), loc=source.loc))
                return self.put_borrowed_binding(stmt.loc, bound_slot(), declaration, value, stmt)
            case th.THIRFieldAccess() | th.THIRSubscript() if (isinstance(source, th.THIRFieldAccess)
                                                               or source.tuple_index is not None):
                tuple_member = isinstance(source, th.THIRSubscript)
                if tuple_member:
                    _require(source, declaration and isinstance(source.receiver, th.THIRName)
                             and source.receiver.name in self.parameters, "tuple borrow needs parameter capture")
                    reference, place = self.projection(source, capture=True)
                else:
                    _require(source, not source.narrowed_deref, "borrow of an Optional field's payload")
                    _require(source, source.form is th.Form.STORAGE, "storage borrow needs record field")
                    reference, place = self.field(source)
                _require(source, reference.cls is holder.cls and reference.type == holder.type,
                         "storage borrow type mismatch")
                _require(stmt, not reference.readonly or holder.readonly, "alias increases access")
                # A tuple member a parameter captures points at its value.
                return self.put_borrow_binding(stmt.loc, bound_slot(), declaration, place, tuple_member, stmt)
            case th.THIRSubscript() if self.container_receiver(source.receiver):
                return self.element_binding(stmt, source, holder, bound_slot, declaration)
            case _ if _user_call(source) or isinstance(source, th.THIRIfExpr):
                value = self.borrowed_expression(source, holder)
                return self.put_borrowed_binding(stmt.loc, bound_slot(), declaration, value, stmt)
        raise MIRUnsupported(stmt, "missing alias binding")

    def optional_declaration(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, shape: MIRShape) -> Fragment:
        """An Optional local of `shape` (its layout fact's): from its source,
        or (`OPT_RVALUE`) a record built into its own backing; a scalar
        payload lives in storage of the declaration's scope."""
        allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "optional_layout", "storage_placement"}
        backing = value = None
        scalar = shape.scalar_wrapper
        if isinstance(stmt, th.THIRVarDecl):
            if not scalar:
                allowed |= {"cpp_local_representation"}
            _require(stmt, stmt.form is (th.Form.VALUE if scalar else th.Form.BORROW)
                     and stmt.init is not None, "optional declaration form or initializer")
            _require(stmt, scalar or stmt.is_const == shape.payload.readonly,
                     "optional declaration access mismatch")
        else:
            allowed |= {"kind"}
            if stmt.kind is th.PtrSlotKind.OPT_RVALUE:
                allowed.add("owned_storage")
                # The declaration kind and its constructed initializer build
                # the payload here (`= None` has the same binding); the
                # payload THIR publishes no storage fact for is refused.
                fact = stmt.optional_layout.payload
                _require(stmt, stmt.owned_storage is not None and isinstance(fact, th.THIRBorrowedRecord)
                         and stmt.is_const == fact.readonly
                         and isinstance(stmt.init, th.THIRCtorCall), "optional backing storage")
                value = self.aggregate_value(stmt.init, fact.type)
                backing = (stmt.init.loc, value_shape(fact.type), _loan(fact))
            else:
                _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE and stmt.init is None,
                         "optional backing storage")
        _plain(stmt, allowed)
        if backing is None:
            value = self.optional_source(stmt.init, shape)
        bound = self.bind(stmt.name, shape, loc=stmt.loc)
        # A holder pointing into a field's payload: a loan replacing the field ends.
        field = isinstance(stmt.init, th.THIRFormConvert) and isinstance(stmt.init.value, th.THIRFieldAccess)
        return self.put_optional_declare(stmt.loc, bound.slot, value, self.placement(stmt) if scalar else None, backing,
                                stmt if field else None, self.missing_placement(stmt, scalar or backing is not None))

    def owned_local(self, stmt: th.THIRVarDecl) -> Fragment:
        typ = stmt.resolved_type
        _require(stmt, stmt.form in (th.Form.VALUE, th.Form.STORAGE), "unsupported local type or form")
        _require(stmt, stmt.name not in self.scope, "duplicate binding")
        # A declaration without a value would default-construct storage no write event names.
        _require(stmt, stmt.init is not None, "owned-leaf declaration needs an initializer")
        value = self.full_expression(stmt.init, check=lambda: self.owned_sink(stmt.init))
        _require(stmt, value.shape_of().type == typ, "initializer type mismatch")
        shape = value_shape(typ)
        self.register(stmt, shape)
        return self.declare(stmt.loc, self.bind(stmt.name, shape, loc=stmt.loc), value)

    def container_literal(self, expr: th.THIRContainerLiteral, typ: NominalType) -> Fragment:
        """A container literal built into container storage of type `typ`:
        one operand per element, each in its member's form -- an inert leaf
        by value, an owned leaf lent for its slot's copy (or built), a record
        constructed into a temporary the literal moves from or copied through
        a holder."""
        _plain(expr, {"elements", "values", "make_container", "elem_cpp", "typed_brace_cpp", "bare_empty"})
        _require(expr, expr.result_type == typ and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                 "container initializer type mismatch")
        members = container_members(typ, False)
        # Literal construction: the one place the compiler knows a container's length.
        _require(expr, len(expr.values) == (0 if len(members) == 1 else len(expr.elements))
                 and (not is_array(typ) or len(expr.elements) == typ.type_args[1]),
                 "container initializer type mismatch")
        operands = (expr.elements if len(members) == 1
                    else tuple(e for pair in zip(expr.elements, expr.values) for e in pair))
        f = self.fragment()
        fields: list[MIRSlotId] = []
        for index, operand in enumerate(operands):
            member = members[index % len(members)]
            match member.cls:
                case MIRClass.LEAF:
                    plan = self.expr(operand, member.type)
                    _require(operand, plan.shape_of().type == member.type, "container element type mismatch")
                case MIRClass.OWNED_LEAF:
                    plan = self.operand(operand, self.owned_value(operand))
                    _require(operand, plan.shape_of().type == member.type, "container element type mismatch")
                case _:
                    plan = self.literal_record(operand, member.type)
            fields.append(f.splice(plan))
        self.argument_order_rule(expr, operands)
        self.writes[expr] = any(self.writes.get(o, False) for o in operands)
        f.value = MIRValue(MIRConstruct(tuple(fields), may_raise=True), value_shape(typ), expr.loc)
        return f

    def literal_record(self, expr: th.THIRExpr, typ: NominalType) -> Fragment:
        """A record element of a container literal (or one handed over by
        value): constructed, or handed over by a call's owned result, into a
        temporary of the full expression, which the literal moves from, or
        copied through the holder a name is."""
        if isinstance(expr, th.THIRCtorCall) or _user_call(expr):
            _require(expr, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
            return self.aggregate_temporary(expr, self.aggregate_value(expr, typ, call=True))
        _require(expr, isinstance(expr, th.THIRCopy), "unsupported container element")
        # A copied element is lent to the literal through a holder of its source.
        return self.holding(self.aggregate_value(expr, typ, copied_source=True))

    def aggregate_temporary(self, expr: th.THIRExpr, value: Fragment) -> Fragment:
        """A record or container built, handed over by a call, copied or
        moved into storage of the full expression for an operation that
        moves it out (a container element, an owning parameter) or lends it.
        THIR publishes no backing for it: it is MIR's own temporary."""
        f = self.fragment()
        built = value.value
        f.splice(value)
        f.value = MIRValue(f.materialize(built, expr), loc=expr.loc)
        return f

    def element_binding(self, stmt: th.THIRStmt, source: th.THIRSubscript, holder: MIRShape,
                        bound_slot: Callable[[], MIRSlotId], declaration: bool) -> Fragment:
        """An aggregate holder bound or rebound to one element in place (`p =
        ps[0]`): a borrow of the container's element region, its index
        evaluated within the binding's full expression."""
        def check() -> Fragment:
            element = self.element_access(source)
            member = element.shape_of()
            _require(stmt, member.cls in AGGREGATE_CLASSES and member.type == holder.type
                     and source.form is th.Form.BORROW and (not member.readonly or holder.readonly),
                     "element binding type or access mismatch")
            v = element.value
            element.value = MIRValue(MIRBorrow(v.value, v.raises), holder, v.loc)
            return element
        value = self.full_expression(source, check=check)
        if declaration:
            return self.put_declare(stmt.loc, bound_slot(), value, None)
        return self.put_assign(stmt.loc, self.valued(MIRValue(MIRPlace(bound_slot()), loc=stmt.loc)), value, None)

    def span_local(self, stmt: th.THIRVarDecl) -> Fragment:
        """A container view local: a holder of the region its source views, readonly
        when its element is or when that region is."""
        typ = unwrap_readonly(stmt.resolved_type)
        _require(stmt, stmt.form is th.Form.VALUE and stmt.init is not None, "unsupported local type or form")
        value = self.full_expression(stmt.init, check=lambda: self.span_value(stmt.init, typ))
        # The holder takes the access its value's region view was decided at (`span_value`).
        shape = value_shape(typ, access=value.shape_of().readonly, role=MIRRole.LOAN)
        self.register(stmt, shape)
        return self.declare(stmt.loc, self.bind(stmt.name, shape, loc=stmt.loc), value)

    def span_value(self, expr: th.THIRExpr, holder: NominalType, target: NominalType | None = None) -> Fragment:
        """Check an expression a container view holder takes, whether the
        region it views is readonly included (`readonly` of the value).
        Every Span-producing operation transfers referents and never copies:
        a Span of the holder's own type (`target`, the outermost holder's)
        is aliased, any other view's region borrowed, an unstepped slice
        borrows its receiver's whole element region after its bounds are
        read (Python slices clamp, so it does not raise)."""
        target = holder if target is None else target
        element = self.register_container(expr, holder)[0].type
        match expr:
            case _ if (self.admitted_place(expr, _SPAN_PLACES) is not None
                       and self.container_view_access(expr.name) is not None):
                _plain(expr, {"name", "is_last_use", "is_movable"})
                source = self.bound(expr.name).type
                _require(expr, container_members(source, False)[0].type == element, "view source type mismatch")
                f = self.place(expr)
                place, loc = f.value.value, expr.loc
                readonly = self.container_view_access(expr.name)
                # Another holder of the same view type is aliased; any other borrows the region it views.
                value = (MIRAlias(f.value.held) if source == target
                         else MIRBorrow(MIRPlace(place.root, (*place.projections, MIRContainerElements()))))
                writing = False
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, region_view(expr.result_type) and region_view(expr.expr.result_type),
                         "unsupported view source")
                f = self.span_value(expr.expr, unwrap_readonly(expr.expr.result_type), target)
                # The coercion views its source in place: the source's value, at this holder's access.
                readonly = f.shape_of().readonly
                value, loc = f.value.value, f.value.loc
                writing = self.writes[expr.expr]
            case th.THIRStrSlice():
                _plain(expr, {"receiver", "cpp_template", "lower", "upper", "step", "stepped", "index"})
                _require(expr, not expr.stepped, "stepped slice")
                _require(expr, expr.step is None and expr.index is None, "unsupported view source")
                bounds = [b for b in (expr.lower, expr.upper) if b is not None]
                plans = []
                for bound in bounds:
                    plans.append(self.expr(bound))
                    _require(bound, int_traits_of(plans[-1].shape_of().type) is not None,
                             "slice bound needs a fixed-width int")
                place = self.container_place(expr.receiver)
                shape = place.shape_of()
                _require(expr, shape.value is None and shape.element.type == element,
                         "view source type mismatch")
                self.argument_order_rule(expr, tuple(bounds))
                f, loc = self.fragment(), expr.loc
                readonly = shape.readonly
                # Its bounds are read, then its receiver's region borrowed.
                for plan in plans:
                    f.splice(plan)
                root = f.splice(place)
                value = MIRBorrow(MIRPlace(root.root, (*root.projections, MIRContainerElements())))
                writing = any(self.writes[b] for b in bounds)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        f.value = MIRValue(value, value_shape(holder, access=loan_access(
            holder, MIRClass.CONTAINER_VIEW, Declared(readonly)), role=MIRRole.LOAN), loc)
        self.writes[expr] = writing
        return f

    def view_local(self, stmt: th.THIRVarDecl) -> Fragment:
        typ = stmt.resolved_type
        _require(stmt, stmt.form is th.Form.VALUE, "unsupported local type or form")
        # A declaration without a value holds an empty view no source names.
        _require(stmt, stmt.init is not None, "view declaration needs an initializer")
        value = self.full_expression(stmt.init, check=lambda: self.view_value(stmt.init, typ))
        return self.declare(stmt.loc, self.bind(stmt.name, view_holder_shape(typ), loc=stmt.loc), value)

    def lent_value(self, expr: th.THIRExpr) -> Fragment:
        """Check an operand an operation reads in place, as its own value: a
        view, or an owned leaf (`owned_value`)."""
        if view_leaf(expr.result_type):
            return self.view_value(expr, expr.result_type)
        return self.owned_value(expr)

    def view_value(self, expr: th.THIRExpr, holder: TpyType) -> Fragment:
        """Check an expression whose value a view holder of type `holder`
        takes, as that value. Every view-producing operation transfers
        referents and never copies: a view is aliased, an owned leaf (a
        name, a global, a static literal, a temporary of the full
        expression) is borrowed, an unstepped slice borrows its whole
        receiver -- whose interior MIR does not model -- after its bounds
        are read, and a call's view result borrows what the call lends."""
        _require(expr, view_leaf(holder), "unsupported view source")
        typ = literal_type(expr) if isinstance(expr, th.THIRLiteral) else expr.result_type
        if not view_leaf(typ):
            source = self.owned_value(expr)
            self.fresh_boundary(expr, source)
            _require(expr, view_compatible(holder, typ), "view source type mismatch")
            # A holder of it is typed by its family's view.
            return source.view(view_holder_shape(holder), expr)
        _require(expr, self.types.get(expr, typ) == typ and view_compatible(holder, typ), "view source type mismatch")
        writing = False
        shape = view_holder_shape(typ)
        match expr:
            case _ if (kind := self.admitted_place(expr, _VIEW_PLACES)) is not None:
                if kind is _Place.NAME:
                    _require(expr, self.view_bound(expr.name), "unsupported view source")
                    _plain(expr, {"name", "is_last_use", "is_movable"})
                    _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
                f = self.place(expr)
                if kind is _Place.ELEMENT:
                    member = f.shape_of()
                    _require(expr, member.cls is MIRClass.OWNED_LEAF and expr.form is th.Form.BORROW
                             and view_compatible(holder, member.type), "view source type mismatch")
                    writing = self.writes[expr.index]
                elif kind is _Place.FIELD:
                    # The loan a view member stores, read whole.
                    member = f.shape_of()
                    _require(expr, member.cls is MIRClass.VIEW and member.type == typ, "view source type mismatch")
                    writing = self.writes.get(expr.receiver, False)
                f.view(shape)
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
                f = self.view_value(expr.expr, typ)
                f.value = MIRValue(f.value.value, shape, expr.loc)
                writing = self.writes[expr.expr]
            case th.THIRStrSlice():
                _plain(expr, {"receiver", "cpp_template", "lower", "upper", "step", "stepped", "index"})
                _require(expr, not expr.stepped, "stepped slice")
                _require(expr, expr.form is th.Form.BORROW and expr.step is None and expr.index is None,
                         "unsupported view source")
                bounds = [b for b in (expr.lower, expr.upper) if b is not None]
                plans = []
                for bound in bounds:
                    plans.append(self.expr(bound))
                    _require(bound, int_traits_of(plans[-1].shape_of().type) is not None,
                             "slice bound needs a fixed-width int")
                receiver = self.view_value(expr.receiver, typ)
                f = self.fragment()
                # The bounds are read, then the whole receiver viewed.
                for plan in plans:
                    f.splice(plan)
                f.value = MIRValue(f.splice(receiver), shape, expr.loc)
                # The bounds and the receiver evaluate in an unspecified order.
                operands = (*bounds, expr.receiver)
                for operand in operands:
                    if self.writes[operand]:
                        _require(expr, all(other is operand or _literal(other) or isinstance(other, th.THIRName)
                                           for other in operands), "order-sensitive eager operands")
                writing = any(self.writes[o] for o in operands)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                f = self.call(expr)
                result = self.calls[expr].borrowed_result
                _require(expr, result is not None and result.type == typ, "call view result mismatch")
                writing = self.call_writes(expr)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        self.types[expr] = typ
        self.writes[expr] = writing
        return f

    def tuple_alias(self, stmt: th.THIRVarDecl) -> None:
        _plain(stmt, {"name", "resolved_type", "init", "tuple_storage_alias", "cpp_local_representation", "cpp_type"})
        fact = stmt.tuple_storage_alias
        _require(stmt, id(stmt) in self.body_declarations, "tuple alias needs unconditional body declaration")
        _require(stmt, isinstance(fact, th.THIRTupleStorageAlias)
                 and isinstance(stmt.init, th.THIRName) and fact.source == stmt.init.name
                 and fact.source in self.tuple_roots, "tuple alias needs initialized local backing")
        _require(stmt, stmt.name not in self.reassigned_locals
                 and fact.source not in self.reassigned_locals
                 and self.tuple_roots[fact.source] not in self.reassigned_locals,
                 "tuple alias needs fixed bindings")
        _require(stmt, stmt.form is th.Form.STORAGE and stmt.resolved_type == stmt.init.result_type,
                 "tuple alias type or form mismatch")
        source = self.tuple_expr(stmt.init, allow_owned=True).shape_of()
        _require(stmt, isinstance(fact.layout, th.THIRTupleLayout) and tuple(map(thir_member, fact.layout.elements))
                 == source.members and source.owned_tuple, "tuple alias layout or access mismatch")
        self.tuple_roots[stmt.name] = self.tuple_roots[fact.source]
        # The alias names the source's own slot.
        self.alias(stmt.name, fact.source)
        return None

    def leaf_layout(self, node: object, typ: NominalType) -> None:
        self.records[typ] = self.definitions.get(node, typ)

    def record_layout(self, node: object, typ: NominalType) -> MIRRecordLayout:
        """The layout of a record the body's storage has, its definition
        registered with the body's layouts; a refused definition refuses."""
        definition = self.records.get(typ)
        # A held layout was decided from the fields alone; storage needs the
        # verified definition whichever registered first.
        if definition is None or isinstance(definition, MIRHeldLayout):
            definition = self.records[typ] = self.definitions.get(node, typ)
        return definition.layout

    def binds_at(self, node: object, storage: NominalType, slot: NominalType) -> bool:
        if slot == storage:
            return True
        return binds_at({storage: self.record_layout(node, storage)}, storage, slot)

    def bound_record(self, arg: th.THIRExpr) -> NominalType | None:
        """The record type of the storage a call argument names, before the
        argument itself is checked; None when it names none."""
        if isinstance(arg, th.THIRArgTemp):
            return unwrap_readonly(arg.result_type)
        if isinstance(arg, th.THIRFieldAccess):
            # An inline member, or a narrowed Optional field's payload, is
            # storage of exactly its declared type.
            fact = arg.field_identity
            bare = unwrap_readonly(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
            if bare is not None and arg.narrowed_deref:
                bare = optional_record_field(bare)
            return bare if bare is not None and record_type(bare) else None
        name = arg.name if isinstance(arg, th.THIRName) else "self" if isinstance(arg, th.THIRSelf) else None
        held = self.holder(name)
        return held.type if held is not None and held.borrowed_record else None

    def close_layouts(self) -> None:
        """Every owned-leaf field of a record the body models is a place of
        that leaf's opaque storage, and every record field a place of its
        member record's storage, so their layouts come along, transitively."""
        pending = list(self.records.values())
        while pending:
            for member in pending.pop().layout.fields:
                if owned_leaf(member.type):
                    self.leaf_layout(self.node, member.type)
                    continue
                bare = inline_member_type(member.type)
                if record_type(bare) and bare not in self.records:
                    self.records[bare] = self.definitions.get(self.node, bare)
                    pending.append(self.records[bare])

    def value(self, expr: th.THIRExpr, expected: TpyType | None = None) -> Fragment:
        """Check an operand, as the slot an operation reads: a view holder,
        an owned leaf read through a borrow, else a scalar value (`expr`)."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        if view_leaf(typ):
            return self.operand(expr, self.view_value(expr, typ))
        if owned_leaf(typ):
            return self.operand(expr, self.owned_value(expr, expected=expected))
        return self.expr(expr, expected)

    def owned_value(self, expr: th.THIRExpr, *, expected: TpyType | None = None) -> Fragment:
        """Check an owned-leaf expression, as its own value: a place (a name,
        a global, a field, an element), fresh storage a build makes, a
        static literal, a holder a borrowed call result is lent by, or a
        passthrough's view of its source. Its consumer converts it
        (`operand`, `owned_sink`, `Fragment.view`)."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        _require(expr, owned_leaf(typ), "unsupported owned-leaf expression type")
        _require(expr, self.types.get(expr, typ) == typ, "literal adopts two types")
        writing = False
        loc = expr.loc
        f = self.fragment()
        match expr:
            case _ if (kind := self.admitted_place(expr, _OWNED_PLACES)) is not None:
                if kind is _Place.NAME:
                    # The binding, not the name's form, says whether it is storage or a borrow.
                    _plain(expr, {"name", "is_last_use", "is_movable"})
                    _require(expr, self.owned_leaf_bound(expr.name) is not None
                             and self.bound(expr.name).type == typ, "non-local name")
                f = self.place(expr)
                if (reason := _OWNED_REFUSALS.get(kind)) is not None:
                    member = f.shape_of()
                    _require(expr, member.cls is MIRClass.OWNED_LEAF and member.type == typ, reason)
                if kind is _Place.ELEMENT:
                    writing = self.writes[expr.index]
            case th.THIRStrLiteral():
                _plain(expr, {"value"})
                _require(expr, expr.form is th.Form.VALUE and owned_constant(typ, expr.value),
                         "unsupported literal value")
                self.literal_use(f, typ, expr)
            case th.THIRBytesLiteral():
                _plain(expr, {"value"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
                _require(expr, expr.form in (th.Form.STORAGE, th.Form.BORROW), "borrowed bytes literal at an owning sink")
                self.literal_use(f, typ, expr)
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
                self.literal_use(f, typ, expr)
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                _require(expr, expr.op not in ("&&", "||") and expr.certified_op, "uncertified binary operation")
                left, right = self.promotion(expr, *self.operands(expr))
                if ((self.writes[expr.left] and not _literal(expr.right))
                        or (self.writes[expr.right] and not _literal(expr.left))):
                    raise MIRUnsupported(expr, "order-sensitive eager operands")
                writing = self.writes[expr.left] or self.writes[expr.right]
                operands = (f.splice(left), f.splice(right))
                f.value = MIRValue(MIROp(expr.op, operands, may_raise=True), owned_leaf_shape(typ, MIRRole.STORAGE),
                                   loc)
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                operand = self.value(expr.operand)
                writing = self.writes[expr.operand]
                rvalue = MIROp(expr.resolved.method.name, (f.splice(operand),), may_raise=True)
                f.value = MIRValue(rvalue, owned_leaf_shape(typ, MIRRole.STORAGE), loc)
            case th.THIRCoerce() if expr.owned_passthrough:
                # The source's own storage read as its family's owned type: a
                # view of it, lent through a holder typed by that view, copied
                # where storage takes it.
                _plain(expr, {"expr", "coercion_name"})
                f = self.lent_value(expr.expr)
                writing = self.writes[expr.expr]
                self.fresh_boundary(expr.expr, f)
                f.view(view_holder_shape(_family_view(typ)), expr.expr)
                f.value = MIRValue(f.value.value, f.value.shape, loc)
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name", "wrap"})
                if (refusal := expr.conversion_refusal) is not None:
                    raise MIRUnsupported(expr, refusal)
                operand = self.value(expr.expr)
                writing = self.writes[expr.expr]
                rvalue = MIROp(f"coerce:{expr.coercion_name}", (f.splice(operand),), may_raise=True)
                f.value = MIRValue(rvalue, owned_leaf_shape(typ, MIRRole.STORAGE), loc)
            case th.THIRFormConvert() if view_leaf(expr.value.result_type):
                # A view at an owning sink: a copy of what it views.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW,
                         "unsupported form conversion")
                f = self.view_value(expr.value, expr.value.result_type)
                _require(expr, view_compatible(expr.value.result_type, typ), "form conversion type mismatch")
                writing = self.writes[expr.value]
                f.sink(owned_leaf_shape(typ, MIRRole.STORAGE))
                f.value = MIRValue(f.value.value, f.value.shape, loc)
            case th.THIRFormConvert():
                # Only the copy of a borrowed read into owned storage: moves,
                # materialized views and generic conversions are not leaf values.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW
                         and isinstance(expr.value, (th.THIRName, th.THIRModuleVar)), "unsupported form conversion")
                f = self.owned_value(expr.value)
                _require(expr, f.shape_of().type == typ, "form conversion type mismatch")
                f.sink(owned_leaf_shape(typ, MIRRole.STORAGE))
                f.value = MIRValue(f.value.value, f.value.shape, loc)
            case th.THIRStrSlice() if expr.stepped:
                raise MIRUnsupported(expr, "stepped slice")
            case th.THIRMove():
                # A move of the body's own whole buffer, a by-value parameter's;
                # a move out of a place is not modeled.
                _plain(expr, {"value"})
                name = expr.value
                _require(expr, isinstance(name, th.THIRName) and name.global_binding is None
                         and name.name in self.parameters and self.owned_leaf_bound(name.name) is True
                         and self.bound(name.name).type == typ, "owned-leaf move")
                _plain(name, {"name", "is_last_use", "is_movable"})
                f.value = MIRValue(MIRMove(self.slot_of(name.name)), owned_leaf_shape(typ, MIRRole.STORAGE), loc)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                # The callee returns an owned leaf by value: fresh storage for the
                # caller, unless a stub's result may borrow what the call lends --
                # a borrow of those arguments, through the holder it is lent by.
                f = self.call(expr)
                summary = self.calls[expr]
                result = summary.borrowed_result
                _require(expr, owned_value_type(signature_result(summary.callee.signature)) == typ
                         and (result is None or result.type == typ and result.readonly),
                         "call owned result mismatch")
                writing = self.call_writes(expr)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                f = self.method_call(expr)
                summary = self.calls[expr]
                _require(expr, owned_value_type(signature_result(summary.callee.signature)) == typ
                         and summary.borrowed_result is None, "call owned result mismatch")
                writing = self.call_writes(expr)
            case _:
                raise MIRUnsupported(expr, "unsupported owned-leaf expression")
        self.leaf_layout(expr, typ)
        self.types[expr] = typ
        self.writes[expr] = writing
        return f

    def literal_use(self, f: Fragment, typ: TpyType, expr: th.THIRExpr) -> None:
        """A literal: static storage lent in place (a str or a borrow-form
        bytes literal), else a value built into storage."""
        f.value = MIRValue(MIRConstant(expr.value), owned_leaf_shape(typ, owned_leaf_role(expr)), expr.loc)

    def aggregate_value(self, expr: th.THIRExpr, typ: NominalType, *, temporary: bool = False,
                        call: bool = False, copied_source: bool = False) -> Fragment:
        """Check what record or container storage of type `typ` takes: a
        construct (a record's constructor, a container's literal), a copy of
        an aggregate source (`aggregate_source`: spelled `copy(x)`, or a
        borrowed name, a member read or a form conversion THIR leaves to its
        form), a move of a fixed owned local, or -- where `call` admits it --
        a call handing over an owned result. `copied_source`: a copy's source
        itself, which a container literal reads through a holder."""
        cls = value_class(typ)
        self.define(expr, typ)
        _require(expr, unwrap_readonly(expr.result_type) == typ,
                 "record initializer type mismatch")
        writing = False
        f = self.fragment()
        source = None
        match expr:
            case th.THIRContainerLiteral() if cls is MIRClass.CONTAINER:
                return self.container_literal(expr, typ)
            case th.THIRCtorCall() if cls is MIRClass.RECORD:
                # A construction is a call of the record's constructor: its
                # result handed over into the destination.
                _require(expr, expr.form is th.Form.STORAGE, "constructor form")
                f = self.call(expr, temporary=temporary)
                writing = self.call_writes(expr)
            case th.THIRCopy():
                _plain(expr, {"value", "cpp_type"})
                source = self.aggregate_source(expr.value, cls)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, self.copyable(typ) and expr.form is th.Form.STORAGE,
                         "record is not copyable or copy form")
            case th.THIRMove():
                _plain(expr, {"value"})
                name = self.reference_name(expr.value)
                _require(expr, self.holder(name).type == typ, "record source type mismatch")
                # A move hands over the local's own storage, spelled in its
                # operand's form or as the storage it moves.
                _require(expr, self.movable(typ) and name in self.fixed_owned
                         and not self.holder(name).readonly
                         and expr.form in (expr.value.form, th.Form.STORAGE), "move needs fixed movable owned local")
                f.value = MIRValue(MIRMove(self.scope[name].backing), value_shape(typ), expr.loc)
            case (th.THIRName() | th.THIRSelf()) if expr.form is th.Form.BORROW:
                # A borrowed name read into storage: C++ copies what it names
                # (sema warned about the inline copy).
                source = self.aggregate_source(expr, cls)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, self.copyable(typ), "record is not copyable or copy form")
            case th.THIRFieldAccess() if expr.form is th.Form.STORAGE:
                # A member read into storage: a copy of that member.
                source = self.aggregate_source(expr, cls)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, self.copyable(typ), "record is not copyable or copy form")
            case th.THIRFormConvert():
                # A local's storage converted into storage: a copy of it.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and isinstance(expr.value, (th.THIRName, th.THIRSelf))
                         and expr.value.form is th.Form.BORROW, "unsupported form conversion")
                source = self.aggregate_source(expr.value, cls)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, self.copyable(typ), "record is not copyable or copy form")
            case th.THIRCall() | th.THIRMethodCall() if call and _user_call(expr):
                call_plan = self.call(expr, temporary=temporary)
                summary = self.calls[expr]
                # The callee's contract, not the destination, says whether the
                # result is fresh: a borrowed result would be copied from its
                # referent. The summary's borrowed result is the signature's
                # (a follows-receiver one differs only in access), so
                # `hands_over` decides; this check only names the refusal.
                _require(expr, summary.borrowed_result is None, "owned result from a borrowed call")
                _require(expr, summary.callee.signature.hands_over(typ) and expr.form is th.Form.STORAGE,
                         "call record result mismatch")
                writing = self.call_writes(expr)
                # The callee's owned result, handed over into the destination.
                f = call_plan
            case _:
                raise MIRUnsupported(expr, "unsupported record initializer")
        self.writes[expr] = writing
        if copied_source:
            return source
        if source is not None:
            # C++ copies what it reads into the storage.
            copy = source.sink(value_shape(typ)).value
            f.splice(source)
            f.value = MIRValue(copy.value, copy.shape, expr.loc)
        return f

    def optional_operand(self, arg: th.THIRExpr, shape: MIRShape) -> Fragment:
        """An argument an Optional parameter whose value is `shape` binds, at
        a call and a constructor alike, as an Optional holder. A record payload
        (`P | None`, a pointer): null, the address of a record source or of
        a temporary built for the call, an Optional field's payload
        (`optional_to_ptr`), or an Optional holder's own pointer; what the
        callee writes through the payload and whether its result borrows it
        is its summary's to say. A scalar payload (`int32 | None`) takes
        what an Optional declaration takes."""
        target = shape.payload
        if not shape.record_payload:
            value = self.optional_source(arg, shape)
            self.writes.setdefault(arg, False)
            f = self.fragment()
            return self.argument_holder(f, arg, shape, f.splice(value), scoped=True)
        f = self.fragment()
        match arg:
            case th.THIRArgTemp():
                temporary = self.argument_temporary(arg, target, addr_of=True)
                value: object = MIRWrap(1, MIRPlace(temporary.holder))
            case th.THIROptionalPtrArg(value=None):
                _plain(arg, set())
                value = None
            case th.THIROptionalPtrArg(lift=True):
                _plain(arg, {"value", "lift"})
                found = self.field(arg.value) if isinstance(arg.value, th.THIRFieldAccess) else None
                _require(arg, found is not None and found[0].cls is MIRClass.OPTIONAL
                         and found[0].payload.cls is MIRClass.RECORD, "unsupported optional argument")
                self.payload_compatible(arg, inline_payload_holder(found[0], self.layouts()), target)
                value = self.wrapper_copy(f.splice(found[1]), shape)
            case th.THIROptionalPtrArg():
                _plain(arg, {"value", "addr_of"})
                _require(arg, isinstance(arg.value, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)),
                         "unsupported optional argument")
                source = self.aggregate_source(arg.value, MIRClass.RECORD)
                self.payload_compatible(arg, source.holder_shape_of(), target)
                value = MIRWrap(1, MIRPlace(f.lend(source)))
            case th.THIRName() if self.wraps(arg.name, MIRClass.OPTIONAL):
                # A narrowed name still passes its holder's pointer.
                extract = arg.optional_read is not None and arg.optional_read.extract
                self.payload_compatible(arg, thir_member(self.optional_name(arg, extract=extract).payload), target)
                value = self.wrapper_copy(MIRPlace(self.slot_of(arg.name)), shape)
            case _:
                raise MIRUnsupported(arg, "unsupported optional argument")
        _require(arg, arg.form is th.Form.BORROW, "unsupported optional argument")
        self.writes[arg] = False
        return self.argument_holder(f, arg, shape, MIRWrap(0) if value is None else value, scoped=False)

    def argument_holder(self, f: Fragment, arg: th.THIRExpr, shape: MIRShape, value: object, *,
                        scoped: bool) -> Fragment:
        """The temporary of the call a wrapper argument of `shape` is written
        into, as `f`'s value. A `scoped` one (a scalar payload) lives in the
        region the call runs in, like a scalar local's."""
        if scoped:
            # A temporary of the full expression, so a loop condition initializes it once per pass.
            _require(arg, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
            self.mir.active_temporaries.append(arg)
            dest = f.slot(shape, storage_duration=f.scoped_duration())
            f.write(dest, value, arg.loc, f.payload_write(shape.scalar_wrapper, MIRStorageWriteMode.INITIALIZE))
        else:
            dest = f.slot(shape)
            f.write(dest, value, arg.loc)
        f.value = MIRValue(dest, loc=arg.loc)
        return f

    def wrapper_argument(self, arg: th.THIRExpr, shape: MIRShape) -> Fragment:
        """A union or tuple argument a parameter whose value is `shape`
        binds: the value a declaration of it takes (`union_source`,
        `tuple_expr`) in a temporary of the call -- a union lift's member
        (`None`, a member-typed name, a union name into a readonly
        parameter), a union name, a tuple literal or name. A lift of a
        hoisted constructor temporary has no placement THIR plans."""
        expr: th.THIRExpr | None = arg
        if isinstance(arg, th.THIRUnionArgLift):
            _plain(arg, {"variant_cpp", "value", "deref", "const_wrap", "temp_cpp"})
            _require(arg, arg.temp_cpp is None, "unsupported union argument temporary")
            expr = arg.value
        f = self.fragment()
        if shape.cls is MIRClass.UNION:
            value = self.union_source(expr, shape, at=arg)
        else:
            value = self.tuple_expr(expr)
        self.writes[arg] = False
        return self.argument_holder(f, arg, value.shape_of(), f.splice(value), scoped=shape.scalar_wrapper)

    def aggregate_operand(self, arg: th.THIRExpr, typ: NominalType) -> Fragment:
        """A record or container argument built into storage of its full
        expression: a construct or literal, a call handing over its owned
        result, a copy, or a fixed owned local moved out."""
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRContainerLiteral, th.THIRCopy, th.THIRMove))
                 or _user_call(arg), "constructor argument type")
        # The temporary stores the loans its construct, the callee's
        # transfers or the copied or moved object name.
        _require(arg, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
        return self.aggregate_temporary(arg, self.aggregate_value(arg, typ, call=True))

    def owned(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> Fragment:
        """A local owning its record or container storage, reached through its holder."""
        holder = _aggregate_holder(stmt.resolved_type, _declared_const(stmt))
        self.reference(stmt, holder, stmt.resolved_type)
        allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "owned_storage", "storage_placement",
                   "native_container"}
        if isinstance(stmt, th.THIRVarDecl):
            allowed.add("cpp_local_representation")
        else:
            allowed.add("kind")
            _require(stmt, stmt.kind in (th.PtrSlotKind.RECORD_RVALUE, th.PtrSlotKind.RECORD_HOISTED),
                     "owned declaration kind")
        _plain(stmt, allowed)
        placement = self.owned_placement(stmt)
        _require(stmt, stmt.init is not None, "owned declaration initializer or access")

        # A constructor argument may need a temporary (an owned leaf built for
        # it), which the declaration's full expression owns.
        hoisted = placement is True
        value = (self.site_fill(stmt.init, holder.type, call=True) if hoisted else
                 self.full_expression(stmt.init, check=lambda: self.aggregate_value(stmt.init, holder.type, call=True)))
        if hoisted:
            _require(stmt, self.movable(holder.type), "hoisted backing needs movable record")
        self.owned_locals.add(stmt.name)
        bound = self.bind(stmt.name, holder, None if hoisted else value_shape(holder.type), loc=stmt.loc)
        if hoisted:
            bound = self.scope[stmt.name] = _Bound(bound.slot, self.site_backing(stmt, holder.type, hoisted=True))
        # The C++ slot is the storage itself: never reseated, assigned in place.
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form in (th.Form.VALUE, th.Form.STORAGE)
                and stmt.name not in self.reassigned_locals):
            self.fixed_owned.add(stmt.name)
        # A hoisted backing's storage is the body's, initialized at its own site.
        return self.put_owned_declare(stmt.loc, bound.slot, bound.backing, value, placement, hoisted,
                                             stmt if placement is None else None)

    def replaces(self, stmt: th.THIRAssign) -> bool:
        """Whether `x = v` builds a new record or container for local `x`
        rather than reseating it: THIR's verdict where a holder's new
        referent is built (`rebind_storage`), or a local whose C++ slot is
        its storage (`fixed_owned`), which is assigned in place."""
        target = stmt.target
        return stmt.rebind_storage is not None or (isinstance(target, th.THIRName) and target.global_binding is None
                                                   and target.name in self.fixed_owned)

    def replacement(self, stmt: th.THIRAssign) -> Fragment:
        """`x = v` building a new record or container for local `x`
        (`replaces`) from what aggregate storage takes: into storage of its
        own (OWN), or into the referent in place (IN_PLACE, or a local whose
        slot is its storage)."""
        _plain(stmt, {"target", "value", "rebind_storage", "slot_cpp"})
        name = self.reference_name(stmt.target)
        shape = self.bound(name)
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        mode = stmt.rebind_storage if stmt.rebind_storage is not None else RebindStorage.IN_PLACE
        _require(stmt, mode in (RebindStorage.OWN, RebindStorage.IN_PLACE), "unsupported replacement storage")
        _require(stmt, mode is not RebindStorage.IN_PLACE or not shape.readonly, "readonly in-place replacement")
        typ = shape.type
        # Its argument temporaries end with the replacing full expression; a
        # fill of a slot of its own builds the value into a temporary of it.
        replacing = (self.site_fill(stmt.value, typ, call=True) if mode is RebindStorage.OWN else
                     self.full_expression(stmt.value, check=lambda: self.aggregate_value(stmt.value, typ, call=True)))
        _require(stmt, self.movable(typ), "replacement needs movable record")
        # A STORAGE-form local is one sema never rebinds (a rebound local is a
        # rebind-slot pointer local), so `fixed_owned` is a whole-body fact; a
        # reseat of a member would hand a returned backing back stale.
        _require(stmt, mode is not RebindStorage.OWN or name not in self.fixed_owned, "fixed owned local reseated")
        # OWN builds the new referent into storage of its own, IN_PLACE into the old one.
        return self.put_replacement(stmt.loc, self.slot_of(name), replacing,
                                    self.site_backing(stmt, typ) if mode is RebindStorage.OWN else None)

    def field_write(self, node: object, loc: object, shape: MIRShape, place: Fragment, value: th.THIRExpr,
                    mode: MIRStorageWriteMode) -> Fragment:
        """A write of field `place` (holding `shape`) from `value`, as `mode`
        says: the one field-write dispatch an assignment `r.f = v` (IN_PLACE)
        and a constructor's member-init entry (INITIALIZE) both take. `node`
        answers for a refusal of the write itself."""
        if shape.cls is MIRClass.OWNED_LEAF:
            # Replaces the field's buffer in place: a write event on the field place.
            written = self.full_expression(value, check=lambda: self.owned_sink(value))
            _require(node, written.shape_of().type == shape.type, "assignment type mismatch")
            return self.put_assign(loc, place, written, mode)
        if shape.cls is MIRClass.OPTIONAL:
            return self.optional_field_write(loc, shape, place, value, mode)
        if shape.cls in AGGREGATE_CLASSES:
            return self.member_write(node, loc, shape, place, value, mode)
        if shape.cls is MIRClass.VIEW:
            return self.view_member_write(loc, shape, place, value)
        written = self.full_expression(value, expected=shape.type)
        _require(node, shape.type == written.shape_of().type, "assignment type mismatch")
        return self.put_scalar_assign(loc, place, written)

    def member_write(self, node: object, loc: object, member: MIRShape, place: Fragment, value: th.THIRExpr,
                     mode: MIRStorageWriteMode) -> Fragment:
        """`ln.a = v`, `self.items = data`: an inline record or container
        member replaced whole, in place -- the record around it keeps its
        identity, so the write is an event of the member place only. The
        value is what aggregate storage takes (`aggregate_value`); C++
        copy- or move-assigns it."""
        # Its argument temporaries end with the replacing full expression.
        replacing = self.full_expression(value, check=lambda: self.aggregate_value(value, member.type, call=True))
        # C++ move-assigns a replacement; an initialization constructs in place.
        _require(node, mode is MIRStorageWriteMode.INITIALIZE or self.movable(member.type),
                 "record member replacement needs movable record")
        return self.put_assign(loc, place, replacing, mode)

    def optional_field_write(self, loc: object, whole: MIRShape, target: Fragment, value: th.THIRExpr,
                             mode: MIRStorageWriteMode) -> Fragment:
        """`h.p = v` on an Optional record field: the whole field replaced in
        place -- the record around it keeps its identity, so the write is an
        event of the field place, its payload included. The value empties
        it, copies another Optional's payload (a whole Optional field, or an
        Optional holder's payload), or is a record built, handed over or
        copied into storage of the statement and moved into the payload."""
        payload = whole.payload.type

        def check() -> Fragment:
            f = self.fragment()
            if isinstance(value, th.THIRLiteral) and value.value is None:
                _plain(value, {"value", "none_cpp"})
                _require(value, isinstance(value.result_type, (OptionalType, NoneType, VoidType)),
                         "optional absence type")
                self.writes[value] = False
                f.value = MIRValue(MIRWrap(0), whole, value.loc)
                return f
            if isinstance(value, th.THIRFieldAccess):
                # THIR spells a copy of a narrowed payload as a bare read of it,
                # with no copy node for MIR to build the record from.
                _require(value, not value.narrowed_deref, "optional field copy from a narrowed payload")
                source, place = self.field(value)
                _require(value, source.cls is MIRClass.OPTIONAL and source.payload.type == payload,
                         "optional field copy source mismatch")
            elif isinstance(value, th.THIRFormConvert):
                _plain(value, {"value", "is_const"})
                name = value.value
                _require(value, value.form is th.Form.STORAGE and isinstance(name, th.THIRName)
                         and self.wraps(name.name, MIRClass.OPTIONAL), "unsupported optional field value")
                layout = self.optional_name(name, extract=False)
                _require(value, isinstance(layout.payload, th.THIRBorrowedRecord)
                         and layout.payload.type == payload, "optional field copy source mismatch")
                place = self.name_place(name.name, name.loc)
            else:
                # Built, handed over or copied into storage of the statement,
                # then moved into the payload.
                temporary = self.aggregate_operand(value, payload)
                _require(value, self.movable(payload), "optional field payload needs movable record")
                f.value = MIRValue(MIRWrap(1, MIRPlace(f.splice(temporary))), whole, value.loc)
                return f
            _require(value, self.copyable(payload), "record is not copyable or copy form")
            self.writes[value] = False
            source = f.splice(place)
            f.value = MIRValue(self.wrapper_copy(source, whole), whole, value.loc)
            return f
        return self.put_assign(loc, target, self.full_expression(value, check=check), mode)

    def view_member_write(self, loc: object, shape: MIRShape, place: Fragment, value: th.THIRExpr) -> Fragment:
        """`t.s = v`: a view member rebound to the loan `v` holds. The record
        keeps its identity and no storage is replaced; every object the
        place may reach stores the loan (`dependencies.store_loan`)."""
        # A loan held by a temporary of the statement ends with it, which
        # the scope-end analysis reports.
        return self.put_view_member_assign(loc, place, self.full_expression(
            value, check=lambda: self.holding(self.view_value(value, shape.type))))

    def reference(self, node: object, ref: MIRShape, typ: TpyType) -> None:
        """Check the shape of a record or container a holder or a wrapper
        member reaches against the type it is read at."""
        _require(node, ref.cls in AGGREGATE_CLASSES, "invalid reference fact")
        problem = shape_problem(ref, typ)
        _require(node, problem is None, problem)

    def lent_loans(self, arg: th.THIRExpr, storage: NominalType, slot: NominalType) -> None:
        """A record lent to a callee whose parameter type holds a loan (`slot`,
        or the argument's own `storage`): the callee may read or store the
        loans the object stores, so the body models them -- the argument's
        layout registered, whose members the dependency pass keys (a
        parameter's seeded at entry; a member's under the object around it,
        whose layout is registered too). A record holding no loan has no
        view member any lowered body reads."""
        if not (holds_loan(slot) or holds_loan(storage)):
            return
        self.held_layout(arg, storage)
        self.held_name(arg, _root_name(arg))

    def held_name(self, node: object, name: str | None) -> None:
        """The layout of the record a place's root binding `name` holds: a
        view member under it, through inline members, is keyed under that
        object, so the entry exists only with the root's layout (a borrowed
        parameter's included, which nothing else registers)."""
        if (held := self.holder(name)) is not None and value_class(held.type) is MIRClass.RECORD:
            # Only a record has view members (a container's are its elements).
            self.held_layout(node, held.type)

    def walked(self, node: object, shape: MIRShape, projections: tuple[Projection, ...], reason: str) -> MIRWalk:
        """`projections` walked from a value of `shape` over the body's
        layouts; a hop the walk refuses refuses `node` for `reason`."""
        try:
            return walk(shape, projections, self.layouts())
        except MIRValidationError:
            raise MIRUnsupported(node, reason) from None

    def held_layout(self, node: object, typ: NominalType) -> MIRRecordLayout:
        """The layout of a record the body reaches through a holder, which a
        caller need not be able to construct (`MIRDefinitions.held_layout`)."""
        if typ not in self.records:
            self.records[typ] = self.definitions.held_layout(node, typ)
        return self.records[typ].layout

    def member_definition(self, node: object, typ: NominalType) -> None:
        """Register what places under an aggregate member of type `typ`
        reach: a container member's definition (`define`). A record
        member's layout needs no constructor and may be one the
        definitions do not carry (`held_layout` would refuse it), so it
        comes only with a walk under it and the layouts' closure
        (`close_layouts`)."""
        if value_class(typ) is MIRClass.CONTAINER:
            self.define(node, typ)

    def register_container(self, node: object, typ: TpyType) -> tuple[MIRShape, ...]:
        """The mutable-access members of a native container (or of the region a
        Span views); its record members' definitions and owned-leaf members'
        layouts come along, since places of its elements reach them."""
        bare = unwrap_readonly(typ)
        _require(node, isinstance(bare, NominalType) and (native_container_type(bare) or container_view(bare)),
                 "unsupported native container type")
        definition = self.define(node, bare)
        _require(node, isinstance(definition, MIRContainerDefinition), "unsupported native container type")
        return definition.members

    def define(self, node: object, typ: NominalType
               ) -> MIRConstructorDefinition | MIRContainerDefinition | MIROwnedLeafDefinition:
        """The verified definition of storage of type `typ`
        (`MIRDefinitions.get`), registered with the body's layouts: a
        record's definition; a container's own (`containers`), its record
        members' definitions and owned-leaf members' layouts, since places of
        its elements reach them."""
        definition = self.definitions.get(node, typ)
        if isinstance(definition, MIRContainerDefinition):
            self.containers.add(typ)
            for record in definition.records:
                self.records[record.layout.type] = record
            for member in definition.members:
                if member.cls is MIRClass.OWNED_LEAF:
                    self.leaf_layout(node, member.type)
        else:
            self.records[typ] = definition
        return definition

    def container_receiver(self, expr: th.THIRExpr) -> bool:
        """Whether `expr` names a container place: a container holder or a
        viewed container binding, a container field, a container a user call
        returns by reference, or a container view a method stub returns."""
        match expr:
            case th.THIRName() if expr.global_binding is None:
                return (self.named_aggregate(expr) is MIRClass.CONTAINER
                        or self.container_view_access(expr.name) is not None)
            case th.THIRFieldAccess() | th.THIRCall():
                return self.named_aggregate(expr) is MIRClass.CONTAINER
            case th.THIRMethodCall() if _user_call(expr):
                return self.named_aggregate(expr) is MIRClass.CONTAINER
            case th.THIRMethodCall():
                # A container view a method stub returns views its receiver's region.
                return expr.stub_callee is not None and container_view(unwrap_readonly(expr.result_type))
        return False

    def container_place(self, expr: th.THIRExpr) -> Fragment:
        """Check a container place expression, as its place (its members
        under the access the place has); `held` is a binding or call holder
        that is the place."""
        if self.named_aggregate(expr) is MIRClass.CONTAINER:
            return self.aggregate_source(expr, MIRClass.CONTAINER)
        match expr:
            case th.THIRName() if (self.admitted_place(expr, _SPAN_PLACES) is _Place.NAME
                                   and self.container_view_access(expr.name) is not None):
                _plain(expr, {"name", "is_last_use", "is_movable"})
                typ = self.bound(expr.name).type
                _require(expr, native_container_subject(expr.result_type) == typ
                         and expr.form in (th.Form.BORROW, th.Form.VALUE), "container name type mismatch")
            case th.THIRMethodCall() if self.container_receiver(expr):
                return self.method_call(expr, view=True)
            case _:
                raise MIRUnsupported(expr, "container source needs a place")
        # A binding is its own holder.
        return self.place(expr)

    def call_place(self, expr: th.THIRCall | th.THIRMethodCall, cls: MIRClass) -> Fragment:
        """A record or container a user call returns by reference: a place
        of the callee's aggregate (its referents are the summary's return
        origins), at the access the call's result is bound at."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        call = self.call(expr)
        result = self.call_results[expr]
        _require(expr, result is not None and value_class(result.type) is cls
                 and unwrap_readonly(expr.result_type) == result.type,
                 "call container result mismatch")
        # A place's operands (an index, slice bounds) are evaluated
        # before its container, so the call must not write what they read.
        _require(expr, not self.call_writes(expr), "order-sensitive eager operands")
        return self.lent_result(expr, call)

    def element_index(self, expr: th.THIRSubscript, place: MIRShape) -> Fragment:
        """Check a subscript's index or key into a container place of shape
        `place`: a fixed-width int position, or a key of the dict's key type
        (an owned-leaf key is lent, a view of it lent as the view)."""
        index = expr.index
        _, _, keyed = declared_members(place.type)
        # A key addresses the value member, a position the element; a type
        # keyed by its only member, or whose value member no key reaches, is not modeled.
        _require(index, keyed is (place.value is not None), "unsupported keyed container")
        if not keyed:
            plan = self.expr(index)
            _require(index, int_traits_of(plan.shape_of().type) is not None, "element index needs a fixed-width int")
            return plan
        key = place.element.type
        if storage_leaf(key):
            plan = self.expr(index, key)
            _require(index, plan.shape_of().type == key, "element key type mismatch")
            return plan
        plan = self.operand(index, self.lent_value(index))
        _require(index, through_view(self.lent_type(index, plan)) == key, "element key type mismatch")
        return plan

    def element_access(self, expr: th.THIRSubscript) -> Fragment:
        """Check a container subscript as a whole: its place, its index, and
        that the operands cannot reorder a write; the element region it
        names (`raises`: the index is not proven in range, or a key may be
        missing). The stub binding a native receiver's read renders through
        is spelling only: the type's element contract is what is modeled."""
        _plain(expr, {"receiver", "index", "bounds_safe", "bound_getitem", "element_access", "unchecked_getitem"})
        _require(expr, expr.tuple_index is None, "tuple projection of a container")
        place = self.container_place(expr.receiver)
        shape = place.shape_of()
        keyed = shape.value is not None
        index = self.element_index(expr, shape)
        _require(expr, not self.writes[expr.index] or _literal(expr.index), "order-sensitive eager operands")
        e = self.fragment()
        # Its index or key is evaluated, then its container named: any
        # element, never one by identity.
        index_loc = index.value.loc
        evaluated = e.splice(index)
        if keyed and shape.element.cls is not MIRClass.LEAF:
            # An owned-leaf key is read by the lookup (its hash and its
            # comparisons), so its holder stays live through the access.
            e.leaf(BOOL, MIROp("key", (evaluated,), may_raise=True), index_loc)
        root = e.splice(place)
        e.value = MIRValue(MIRPlace(root.root, (*root.projections, MIRContainerElements())), loc=expr.loc,
                           raises=not expr.bounds_safe or keyed)
        return e

    def reference_name(self, expr: th.THIRExpr, *, qualified: bool = False) -> str:
        """The binding a reference names: a record's or a container's holder
        (`holder`). With `qualified`, the receiver of an explicit ancestor
        field (`Base.n` in a method) may be spelled at an ancestor of the
        receiver's type."""
        match expr:
            case th.THIRName():
                _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
                name = expr.name
            case th.THIRSelf():
                # `cpp` spells the receiver for C++ only.
                _plain(expr, {"deref", "is_last_use", "is_movable", "pointer", "cpp"})
                # A frame holds its receiver as a reference, a function body as
                # the pointer `this`: either lends the caller's record.
                _require(expr, expr.pointer is (self.frame is None), "unsupported metadata: pointer")
                receiver = self.header.receiver
                _require(expr, receiver is not None, "missing receiver fact")
                _require(expr, expr.form is th.Form.BORROW, "receiver read form")
                _require(expr, not isinstance(expr.result_type, ReadonlyType)
                         or receiver.readonly, "receiver read increases access")
                name = "self"
            case _:
                raise MIRUnsupported(expr, "reference needs local name")
        held = self.holder(name)
        _require(expr, held is not None, "unknown reference source")
        named = unwrap_readonly(expr.result_type)
        if qualified and isinstance(expr, th.THIRSelf) and named != held.type:
            _require(expr, isinstance(named, NominalType) and self.binds_at(expr, held.type, named),
                     "reference type mismatch")
            return name
        # An `Own[...]` binding's name reads the storage it owns.
        self.reference(expr, held, unwrap_own(expr.result_type))
        return name

    def aggregate_place(self, expr: th.THIRExpr) -> tuple[_Source, MIRClass | None] | None:
        """The one classification of an aggregate source: which kind of
        source `expr` is (`aggregate_source` dispatches on it), and the
        class of the existing aggregate storage it names in place, None
        when it names none of that kind -- a holder's name or `self`, a
        narrowed Optional record name, a member read in place, a user call
        whose summary (`call_summary`, what `call` binds) returns one by
        reference. None for any other expression."""
        if _user_call(expr):
            summary = self.call_summary(expr)
            result = None if isinstance(summary, str) else summary[0].result_at(False)
            return _Source.CALL, _aggregate_class(result.type) if result is not None else None
        match self.admitted_place(expr, _AGGREGATE_PLACES):
            case _Place.FIELD:
                fact = expr.field_identity
                bare = unwrap_readonly(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
                if bare is not None and expr.narrowed_deref:
                    bare = optional_record_field(bare)
                in_place = bare is not None and expr.form in (th.Form.STORAGE, th.Form.BORROW)
                return _Source.MEMBER, _aggregate_class(bare) if in_place else None
            case _Place.NAME if self.wraps(expr.name, MIRClass.OPTIONAL):
                return _Source.OPTIONAL_NAME, MIRClass.RECORD
            case _Place.NAME | _Place.SELF:
                held = self.holder("self" if isinstance(expr, th.THIRSelf) else expr.name)
                return _Source.HOLDER, _aggregate_class(held.type) if held is not None else None
        return None

    def named_aggregate(self, expr: th.THIRExpr) -> MIRClass | None:
        """The class of the existing aggregate storage `expr` names in place
        (`aggregate_place`), None for any other expression."""
        found = self.aggregate_place(expr)
        return found[1] if found is not None else None

    def member_source(self, expr: th.THIRFieldAccess, cls: MIRClass) -> Fragment:
        """An aggregate member read in place (`ln.a`, `self.items`), at the
        access it is reached with, as that place."""
        _require(expr, expr.form in (th.Form.STORAGE, th.Form.BORROW), "record member read form")
        s = self.place(expr)
        _require(expr, s.shape_of().cls is cls, "record source needs record storage")
        return s

    def aggregate_source(self, expr: th.THIRExpr, cls: MIRClass) -> Fragment:
        """The record or container storage an expression names in place, at
        the access it is reached with, as that place: a name or `self` (what
        its holder reaches; `held` is that holder) read in its borrow form or
        in its own storage's (`fixed_owned`), a member read through them
        (`ln.a`, `self.items`), a narrowed Optional record name's payload, or
        a user call's borrowed result. Reseats, aliases and wrapper captures
        keep needing a name (`reference_name`)."""
        found = self.aggregate_place(expr)
        kind = found[0] if found is not None else None
        if kind is _Source.CALL:
            return self.call_place(expr, cls)
        if kind is _Source.MEMBER:
            return self.member_source(expr, cls)
        if kind is _Source.OPTIONAL_NAME:
            # A narrowed Optional name: the record its payload points at.
            source = self.optional_name(expr, extract=True).payload
            _require(expr, cls is MIRClass.RECORD and isinstance(source, th.THIRBorrowedRecord),
                     "record source needs record storage")
        else:
            # Any other source refuses here: a source needs a name.
            name = self.reference_name(expr)
            _require(expr, self.bound(name).cls is cls, "record source needs record storage")
            _require(expr, not isinstance(expr, th.THIRName) or expr.form is th.Form.BORROW
                     or name in self.fixed_owned and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                     "call needs borrowed record name")
        # A name's own holder points at it and lends it.
        return self.place(expr)

    def borrowed_expression(self, expr: th.THIRExpr, result: MIRShape) -> Fragment:
        """A borrowed result, as the holder slot it is; `result` is the shape
        of a holder of it."""
        with self.argument_order(expr):
            if isinstance(expr, th.THIRFieldAccess):
                # A member the result reaches, borrowed in place (a record's
                # in STORAGE form, as a storage borrow's source); never lent
                # with more access than its path has.
                self.reference(expr, result, expr.result_type)
                source = self.member_source(expr, result.cls)
                shape = source.shape_of()
                _require(expr, shape.type == result.type and (not shape.readonly or result.readonly),
                         "return increases access")
                f = self.fragment()
                # A member place: the result holder borrows it.
                f.value = MIRValue(f.lend(source, result), loc=expr.loc)
                return f
            return self._borrowed_expression(expr, result)

    def _borrowed_expression(self, expr: th.THIRExpr, result: MIRShape) -> Fragment:
        _require(expr, expr.form is (th.Form.VALUE if _user_call(expr) else th.Form.BORROW),
                 "unsupported borrowed expression form")
        self.reference(expr, result, expr.result_type)
        match expr:
            case th.THIRSlotEmplace():
                # The emplacement is the select arm itself: its storage is built where it is chosen.
                temporary = self.select_temporary(expr, result)
                return self.anchored(expr, self.valued(MIRValue(temporary.holder, loc=expr.loc)))
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                call = self.call(expr)
                actual = self.call_results[expr]
                _require(expr, actual is not None and actual.type == result.type
                         and (not actual.readonly or result.readonly), "call result increases access")
                # Held at the result's access, which may narrow the call's.
                f = self.fragment()
                f.value = MIRValue(f.lend(call, result), loc=expr.loc)
                return f
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                cond = self.expr(expr.cond)
                _require(expr, cond.shape_of().type == BOOL, "condition requires bool")
                then = self.anchored(expr.then, self._borrowed_expression(expr.then, result))
                orelse = self.anchored(expr.orelse, self._borrowed_expression(expr.orelse, result))
                f = self.fragment()
                condition = f.splice(cond)
                dest = f.slot(result)
                yes, no, join = f.block(), f.block(), f.block()
                f.branch(condition, yes.id, no.id, expr.loc)
                for block, arm in ((yes, then), (no, orelse)):
                    f.current = block
                    loc = arm.value.loc
                    f.write(dest, MIRAlias(f.splice(arm)), loc)
                    f.end(MIRGoto(join.id, loc))
                f.current = join
                f.value = MIRValue(dest, loc=expr.loc)
                return f
            case _:
                # A name: its holder's referent, as an alias's name source.
                name = self.reference_name(expr)
                source = self.bound(name)
                _require(expr, source.cls is result.cls, "borrowed source needs a holder")
                _require(expr, not source.readonly or result.readonly, "return increases access")
                return self.valued(MIRValue(self.slot_of(name), loc=expr.loc))

    def laid_out(self, node: object, typ: TpyType, *, holder: object = None, others: tuple[str, ...] = (),
                 allow_owned: bool = False) -> MIRShape | None:
        """The value the one layout fact `holder` (`node` by default)
        carries lays out -- a lent record, a wrapper -- read at `typ`,
        checked (`layout_fact`, `fact_shape`), with what its position needs
        registered (`register`; an owning tuple only where `allow_owned`),
        refusals raised against `node`; None when it carries none, or one of
        the facts `others` names (the caller's)."""
        fact = layout_fact(node if holder is None else holder, others=others)
        _require(node, not isinstance(fact, str), fact)
        if fact is None:
            return None
        # An owning tuple's position is its producer's slot.
        shape = fact_shape(typ, fact, producer=allow_owned)
        _require(node, isinstance(shape, MIRShape), shape)
        self.register(node, shape, allow_owned=allow_owned)
        return shape

    def union_name(self, expr: th.THIRName) -> th.THIRUnionLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "union_read", "indirect"})
        fact = expr.union_read
        _require(expr, isinstance(fact, th.THIRUnionLayout) and self.union_is(expr.name, fact),
                 "missing or inconsistent union read")
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW)
                 and unwrap_readonly(expr.result_type) == fact.type,
                 "union read type or form mismatch")
        return fact

    def union_source(self, expr: th.THIRExpr | None, shape: MIRShape, literal: th.THIRUnionLiteral | None = None,
                     layout: th.THIRUnionLayout | None = None, *, at: th.THIRExpr | None = None) -> Fragment:
        """What a union of `shape` takes -- a declaration's, an assignment's
        (its layout fact `layout` beside a literal fact), a parameter's --
        engaging the alternative it chooses; no expression (a `None` lift
        `at` an argument) is the absence."""
        node = expr if expr is not None else at
        f = self.fragment()
        members = shape.members
        if literal is not None:
            _require(expr, isinstance(literal, th.THIRUnionLiteral) and literal.layout == layout
                     and type(literal.alternative) is int and 0 <= literal.alternative < len(members),
                     "invalid union literal fact")
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS, "unsupported union literal coercion")
                expr = expr.expr
            _require(expr, isinstance(expr, th.THIRLiteral), "union literal fact needs literal source")
            _plain(expr, {"value", "int_cpp", "none_cpp"})
            member = members[literal.alternative]
            _require(expr, type(expr.value) is type(literal.value) and expr.value == literal.value
                     and (member is None and literal.value is None
                          or member is not None and member.is_scalar and leaf_constant(member.type, literal.value)),
                     "union literal payload mismatch")
            source = None if member is None else MIRPlace(f.leaf(member.type, MIRConstant(literal.value), node.loc))
            f.value = MIRValue(MIRWrap(literal.alternative, source), shape, node.loc)
            return f
        if isinstance(expr, th.THIRName) and self.wraps(expr.name, MIRClass.UNION):
            source = self.union_name(expr)
            found = value_shape(source.type, facts=source)
            _require(expr, found.type == shape.type, "union copy type mismatch")
            for src, dst in zip(found.members, members):
                if src is None or dst is None:
                    _require(expr, src is dst, "union copy absence mismatch")
                else:
                    self.payload_compatible(expr, src, dst)
            f.value = MIRValue(self.wrapper_copy(MIRPlace(self.slot_of(expr.name)), shape), shape, node.loc)
            return f
        if expr is None or isinstance(expr, th.THIRLiteral) and expr.value is None:
            if expr is not None:
                _plain(expr, {"value", "none_cpp"})
            _require(node, None in members, "union has no absence alternative")
            f.value = MIRValue(MIRWrap(members.index(None)), shape, node.loc)
            return f
        if self.named_aggregate(expr) is MIRClass.RECORD:
            # A record source named in place, lent as an Optional argument's payload is (`aggregate_source`).
            place = self.aggregate_source(expr, MIRClass.RECORD)
            source = place.holder_shape_of()
            matches = [i for i, m in enumerate(members) if m is not None and m.borrowed_record and m.type == source.type]
            _require(expr, len(matches) == 1, "union has no matching record alternative")
            self.payload_compatible(expr, source, members[matches[0]])
            f.value = MIRValue(MIRWrap(matches[0], MIRPlace(f.lend(place))), shape, node.loc)
            return f
        plan = self.expr(expr)
        typ = plan.shape_of().type
        scalars = [m.type if m is not None and m.is_scalar else None for m in members]
        _require(expr, typ in scalars and not self.writes[expr], "unsupported union member construction")
        f.value = MIRValue(MIRWrap(scalars.index(typ), MIRPlace(f.splice(plan))), shape, node.loc)
        return f

    def union_extraction(self, node: object, fact: th.THIRUnionExtraction | None) -> TpyType | th.THIRBorrowedRecord:
        _require(node, isinstance(fact, th.THIRUnionExtraction)
                 and self.union_is(fact.source, fact.layout),
                 "missing or inconsistent union extraction")
        _require(node, type(fact.alternative) is int and 0 <= fact.alternative < len(fact.layout.elements),
                 "invalid union extraction alternative")
        member = fact.layout.elements[fact.alternative]
        _require(node, member is not None, "cannot extract absent union alternative")
        return member

    def inline_union(self, expr: th.THIRNarrowedRead) -> TpyType | th.THIRBorrowedRecord:
        _plain(expr, {"variant_cpp", "member_cpp", "is_ptr_variant", "union_extraction"})
        member = self.union_extraction(expr, expr.union_extraction)
        reference = isinstance(member, th.THIRBorrowedRecord)
        _require(expr, expr.is_ptr_variant is reference
                 and unwrap_readonly(expr.result_type) == (member.type if reference else member)
                 and expr.form is (th.Form.BORROW if reference else th.Form.VALUE), "union extraction form mismatch")
        return member

    def optional_name(self, expr: th.THIRName, *, extract: bool) -> th.THIROptionalLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect", "optional_read"})
        fact = expr.optional_read
        _require(expr, isinstance(fact, th.THIROptionalRead) and fact.extract is extract
                 and self.optional_is(expr.name, fact.layout),
                 "missing or inconsistent optional read")
        layout = fact.layout
        reference = isinstance(layout.payload, th.THIRBorrowedRecord)
        typ = layout.payload.type if reference else layout.payload
        actual = unwrap_readonly(expr.result_type)
        _require(expr, actual == typ if extract else (
            actual in (typ, self.bound(expr.name).type) or isinstance(actual, VoidType)),
            "optional read type mismatch")
        _require(expr, expr.form is (th.Form.BORROW if reference else th.Form.VALUE)
                 and (reference or expr.deref is extract), "optional read form mismatch")
        return layout

    def optional_source(self, expr: th.THIRExpr | None, shape: MIRShape) -> Fragment:
        """What an Optional binding of shape `shape` takes: nothing (empty),
        another Optional's value, a holder of an Optional field's payload, a
        record binding's holder, or a scalar payload."""
        f = self.fragment()
        payload = shape.payload
        loc = expr.loc if expr is not None else None
        if expr is None:
            f.value = MIRValue(MIRWrap(0), shape, loc)
            return f
        node = expr
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            if isinstance(expr.result_type, OptionalType):
                # The same layout read at the literal's own Optional type.
                problem = shape_problem(replace(shape, type=unwrap_readonly(expr.result_type)),
                                        expr.result_type)
                _require(expr, problem is None, problem)
            else:
                _require(expr, isinstance(expr.result_type, (NoneType, VoidType)),
                         "optional absence type")
            f.value = MIRValue(MIRWrap(0), shape, loc)
            return f
        if isinstance(expr, th.THIRName) and self.wraps(expr.name, MIRClass.OPTIONAL) and (
                expr.optional_read is not None and not expr.optional_read.extract):
            source = self.optional_name(expr, extract=False)
            self.payload_compatible(expr, thir_member(source.payload), payload)
            f.value = MIRValue(self.wrapper_copy(MIRPlace(self.slot_of(expr.name)), shape), shape, loc)
            return f
        if isinstance(expr, th.THIRFormConvert) and isinstance(expr.value, th.THIRFieldAccess):
            # A holder of an Optional record field's inline payload (`optional_to_ptr`): a borrow, no copy.
            _plain(expr, {"value", "is_const"})
            source, place = self.field(expr.value)
            _require(expr, source.cls is MIRClass.OPTIONAL and expr.form is th.Form.BORROW
                     and shape.record_payload and expr.is_const == payload.readonly, "unsupported optional field borrow")
            self.payload_compatible(expr, inline_payload_holder(source, self.layouts()), payload)
            f.value = MIRValue(self.wrapper_copy(f.splice(place), shape), shape, loc)
            return f
        if shape.record_payload:
            _require(expr, isinstance(expr, th.THIRName), "optional capture needs local name")
            name = self.reference_name(expr)
            source = self.holder(name)
            _require(expr, source is not None and member_compatible(source, payload),
                     "optional capture access mismatch")
            f.value = MIRValue(MIRWrap(1, MIRPlace(self.slot_of(name))), shape, loc)
            return f
        if _literal(expr):
            # Hoisted assignments contextualize the literal with the wrapper type.
            _require(expr, expr.form is th.Form.VALUE, "unsupported optional literal form")
            payload = payload.type
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS
                         and expr.result_type in (payload, OptionalType(payload)), "unsupported optional literal coercion")
                expr = expr.expr
            _plain(expr, {"value", "int_cpp"})
            typ = literal_type(expr, payload)
            _require(expr, expr.form is th.Form.VALUE and typ in (payload, OptionalType(payload))
                     and leaf_constant(payload, expr.value), "optional literal payload mismatch")
            payload_slot = f.leaf(payload, MIRConstant(expr.value), node.loc)
            f.value = MIRValue(MIRWrap(1, MIRPlace(payload_slot)), shape, loc)
            return f
        plan = self.expr(expr, payload.type)
        _require(expr, plan.shape_of().type == payload.type and not self.writes[expr],
                 "effectful or mistyped optional payload")
        f.value = MIRValue(MIRWrap(1, MIRPlace(f.splice(plan))), shape, loc)
        return f

    def tuple_members(self, node: object, shape: MIRShape, *, allow_owned: bool = False) -> None:
        """What a tuple's position needs of its members: a record stored
        inline only at an owning position, with its movable definition."""
        _require(node, allow_owned or not shape.owned_tuple, "owning tuple position is unsupported")
        for member in shape.elements:
            if member.is_storage:
                definition = self.definitions.get(node, member.type)
                _require(node, definition.layout.movable, "tuple construction needs movable record")
                self.records[member.type] = definition

    def tuple_compatible(self, node: object, source: tuple[MIRShape, ...], target: tuple[MIRShape, ...]) -> None:
        _require(node, len(source) == len(target), "tuple copy arity")
        for src, dst in zip(source, target):
            self.payload_compatible(node, src, dst)

    def payload_compatible(self, node: object, source: MIRShape, target: MIRShape) -> None:
        """A wrapper member copied into another (`member_compatible`)."""
        _require(node, member_compatible(source, target), "payload copy type or access mismatch")

    def tuple_copy(self, value: Fragment, name: str) -> Fragment:
        """A copy of tuple `value` into binding `name`, as one full expression."""
        loc = value.value.loc
        copy = self.fragment()
        copy.value = MIRValue(self.wrapper_copy(MIRPlace(copy.lend(value)), self.bound(name)), self.bound(name), loc)
        return self.whole(copy, loc)

    def tuple_expr(self, expr: th.THIRExpr, *, allow_owned: bool = False) -> Fragment:
        """A tuple value: a copy of a binding's, or one built from its
        elements -- each in its member's form: a record a borrowed member
        names (a binding's slot), a record an owned member builds into a
        temporary of the full expression and moves in (`literal_record`),
        else a value; `holder` is the shape of a slot holding it."""
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW, th.Form.STORAGE), "unsupported tuple form")
        t = self.fragment()
        elements: list[object] = []
        if isinstance(expr, th.THIRName):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, self.wraps(expr.name, MIRClass.TUPLE), "tuple needs local payload")
            bound = self.bound(expr.name)
            layout = bound.members
            # An owning tuple is read only at an owning position, its producer's slot.
            self.tuple_members(expr, bound, allow_owned=allow_owned)
            problem = shape_problem(bound, expr.result_type, producer=allow_owned)
            _require(expr, problem is None, problem)
            # A copy of the binding's value, held as a fresh tuple at a tuple's own access.
            holder = replace(bound, readonly=loan_access(bound.type, MIRClass.TUPLE, None))
        else:
            _require(expr, isinstance(expr, (th.THIRTupleLiteral, th.THIRBorrowTupleLiteral)),
                     "unsupported tuple expression")
            allowed = {"elements", "tuple_layout"}
            if isinstance(expr, th.THIRBorrowTupleLiteral):
                allowed |= {"spelled_cpp", "elem_cpps", "addr_of"}
                _require(expr, len(expr.addr_of) == len(expr.elements), "tuple address arity")
            _plain(expr, allowed)
            holder = self.laid_out(expr, expr.result_type, allow_owned=allow_owned)
            _require(expr, holder is not None, "missing or invalid layout fact")
            layout = holder.members
            _require(expr, len(expr.elements) == len(layout), "tuple capture arity")
            for element, member in zip(expr.elements, expr.tuple_layout.elements):
                if isinstance(member, th.THIROwnedRecord):
                    _require(element, isinstance(expr, th.THIRTupleLiteral)
                             and isinstance(element, th.THIRCtorCall), "inline tuple record needs constructor")
                    elements.append(t.splice(self.literal_record(element, member.type)))
                elif isinstance(member, th.THIRBorrowedRecord):
                    _require(expr, isinstance(expr, th.THIRBorrowTupleLiteral), "value tuple borrows record")
                    if isinstance(element, (th.THIRName, th.THIRSelf)):
                        name = self.reference_name(element)
                        source = self.holder(name)
                        _require(element, source is not None and member_compatible(source, thir_member(member)),
                                 "tuple capture type or access mismatch")
                        elements.append(self.slot_of(name))
                    else:
                        # A member or a borrowed call result, lent where it lies (`aggregate_source`).
                        place = self.aggregate_source(element, MIRClass.RECORD)
                        _require(element, member_compatible(place.holder_shape_of(), thir_member(member)),
                                 "tuple capture type or access mismatch")
                        elements.append(t.lend(place))
                else:
                    plan = self.expr(element, member)
                    _require(element, plan.shape_of().type == member and not self.writes[element],
                             "effectful or mistyped tuple element")
                    elements.append(t.splice(plan))
        _require(expr, expr.form is th.Form.STORAGE if holder.owned_tuple and isinstance(expr, th.THIRName)
                 else expr.form in (th.Form.VALUE, th.Form.BORROW), "tuple form disagrees with layout")
        t.value = MIRValue(self.wrapper_copy(MIRPlace(self.slot_of(expr.name)), holder) if isinstance(expr, th.THIRName)
                           else MIRConstruct(tuple(elements)), holder, expr.loc)
        return t

    def projection(self, expr: th.THIRSubscript, *, capture: bool = False) -> tuple[MIRShape, Fragment]:
        """A tuple member read in place: its member shape, and its place."""
        _plain(expr, {"receiver", "index", "tuple_index", "elem_pointer"}
               | ({"deref"} if capture else set()))
        _require(expr, not capture or expr.deref, "tuple capture needs dereferenced element")
        receiver = self.tuple_expr(expr.receiver, allow_owned=True)
        tuple_shape = receiver.shape_of()
        _require(expr, not tuple_shape.owned_tuple or isinstance(expr.receiver, th.THIRName),
                 "owned tuple projection needs existing local")
        index = expr.tuple_index
        member = self.walked(expr, tuple_shape, (MIRTupleIndex(index),), "missing or invalid tuple index").end
        _require(expr, isinstance(expr.index, th.THIRLiteral)
                 and type(expr.index.value) is int and expr.index.value == index,
                 "tuple index disagreement")
        _plain(expr.index, {"value", "int_cpp"})
        # The render's pointer fact must agree with the layout MIR reads.
        _require(expr, expr.elem_pointer == member.pointer_element,
                 "tuple element pointer disagreement")
        if member.cls is MIRClass.RECORD:
            self.reference(expr, member, expr.result_type)
            _require(expr, expr.form is th.Form.BORROW, "tuple reference projection form")
            if member.is_storage:
                _require(expr, not capture, "owned tuple element capture is unsupported")
        else:
            _require(expr, not capture, "tuple capture needs record element")
            _require(expr, expr.result_type == member.type and expr.form is th.Form.VALUE,
                     "tuple scalar projection type or form")
        place = self.fragment()
        # A tuple member: of a binding, or of a tuple value written into a holder.
        root = (self.slot_of(expr.receiver.name) if isinstance(expr.receiver, th.THIRName)
                else place.lend(receiver))
        place.value = MIRValue(MIRPlace(root, (MIRTupleIndex(index),)), loc=expr.loc)
        return member, place

    def field(self, expr: th.THIRFieldAccess, *, write: bool = False) -> tuple[MIRShape, Fragment]:
        """A field place, and the value it holds (`value_shape` at field position). An
        Optional record field (`optional_record_field`) is the whole field,
        at the access its path reaches it with, or its payload record once
        THIR narrowed the read (`narrowed_deref`)."""
        _refuse_none_checked(expr, expr.receiver, "field read")
        fact = expr.field_identity
        payload = optional_record_field(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
        _plain(expr, {"receiver", "field_cpp", "field_identity", "is_arrow"}
               | ({"narrowed_deref"} if payload is not None else set()))
        receiver = expr.receiver
        # A construct names its storage or refuses; a call only when it hands one over.
        if isinstance(receiver, th.THIRCtorCall) or th.record_rvalue_storage(receiver) is not None:
            _require(expr, not write and not expr.is_arrow, "temporary field requires scalar read")
            reference, base = self.temporary(receiver)
        else:
            kind = place_kind(receiver)
            kind = kind if kind in _RECEIVER_PLACES else None
            optional = kind is _Place.NAME and self.wraps(receiver.name, MIRClass.OPTIONAL)
            if kind is _Place.FIELD:
                # A store under a narrowed payload would replace part of it while
                # MIR keeps the payload whole (`place_info` forbids it).
                _require(expr, not (write and receiver.narrowed_deref), "optional field payload write")
            elif optional:
                self.optional_name(receiver, extract=True)
            elif kind not in (_Place.UNION_PAYLOAD, _Place.ELEMENT, _Place.TUPLE_MEMBER):
                # Any other receiver refuses here: a field is read through a record place.
                self.reference_name(receiver, qualified=True)
            base = self.place(receiver)
            reference = base.shape_of()
            reason = "field needs optional record" if optional else _RECEIVER_REFUSALS.get(kind)
            if reason is not None:
                # A record element is read in place, at the type and form it is borrowed at.
                _require(expr, reference.cls is MIRClass.RECORD and (kind is not _Place.ELEMENT or (
                    receiver.form is th.Form.BORROW
                    and unwrap_readonly(receiver.result_type) == reference.type)), reason)
        _require(expr, isinstance(fact, th.THIRFieldIdentity), "missing field identity")

        def admit(shape: MIRShape) -> None:
            """The THIR access spells the member at the type and form its class reads at."""
            readonly = shape.readonly
            if shape.cls in AGGREGATE_CLASSES:
                bare = unwrap_readonly(fact.type)
                self.reference(expr, value_shape(bare, access=readonly, role=MIRRole.LOAN), expr.result_type)
                _require(expr, expr.form in (th.Form.STORAGE, th.Form.BORROW), "unsupported field type or form")
                _require(expr, not write or expr.form is th.Form.STORAGE,
                         "record member write needs member storage")
            elif shape.cls is MIRClass.OPTIONAL:
                _require(expr, expr.form is th.Form.STORAGE, "unsupported field type or form")
                if expr.narrowed_deref:
                    _require(expr, not write, "optional field payload write")
                    self.reference(expr, value_shape(payload, MIRClass.RECORD, access=readonly,
                                                     role=MIRRole.LOAN), expr.result_type)
                else:
                    # An assign target keeps the narrowed type but writes the whole field.
                    result = unwrap_readonly(expr.result_type)
                    _require(expr, result == unwrap_readonly(fact.type) or write and result == payload,
                             "unsupported field type or form")
            elif shape.cls is MIRClass.VIEW:
                _require(expr, expr.result_type == fact.type and expr.form is th.Form.BORROW,
                         "unsupported field type or form")
            elif shape.cls is MIRClass.OWNED_LEAF:
                # Its form is how the C++ spells the member (a BigInt reads as a
                # value scalar, str as storage); either way it is that lvalue.
                _require(expr, expr.result_type == fact.type and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                         "unsupported field type or form")
            else:
                _require(expr, expr.result_type == fact.type and expr.form is th.Form.VALUE,
                         "unsupported field type or form")
        shape, f = self.member(expr, base, fact, reference.type, _root_name(expr),
                               MIRStorageWriteMode.IN_PLACE if write else None, expr.loc,
                               payload=bool(expr.narrowed_deref), admit=admit)
        if shape.cls is MIRClass.OPTIONAL and expr.narrowed_deref:
            # A narrowed Optional field read names its inline payload.
            shape = step(shape, OPTIONAL_PAYLOAD, self.layouts())
        return shape, f

    def member(self, node: object, base: Fragment, fact: th.THIRFieldIdentity, record: NominalType,
               root: str | None, mode: MIRStorageWriteMode | None, loc: object, *, payload: bool = False,
               admit: Callable[[MIRShape], None] | None = None) -> tuple[MIRShape, Fragment]:
        """Field `fact` of the record `base` reaches (of type `record`, held
        by binding `root`), read (`mode` None) or written as `mode` says:
        its place and the shape it holds, with what places under it reach
        registered. `payload`: the place is a narrowed Optional field's
        inline payload. `admit` checks the access spelling the member once
        its shape is known."""
        def admitted(shape: MIRShape) -> None:
            if admit is not None:
                admit(shape)
        # An identity names a declared member of any type, not only one MIR models.
        _require(node, position_problem(fact.type, MIRPosition.FIELD) is None, "unsupported field type or form")
        member_field = MIRField(MIRFieldId(fact.owner, fact.name), fact.type)
        holder = base.shape_of()
        walked = self.walked(node, holder, (*object_prefix(holder), member_field),
                             "field owner mismatch")
        shape = walked.end
        # A field's first initialization is no store to it: its own `readonly` allows it.
        _require(node, not (mode is not None and store_problem(
            walked, initialization=mode is MIRStorageWriteMode.INITIALIZE)), "readonly field store")
        if shape.cls in AGGREGATE_CLASSES:
            # An inline record or container member: a place of the record's
            # storage, borrowed, copied out, or replaced whole in place
            # (`member_write`); a container's region is reached through it.
            bare = unwrap_readonly(fact.type)
            problem = admission_problem(bare, shape.cls)
            _require(node, problem is None, problem)
            # A malformed access refuses as such before any registration can refuse the record.
            admitted(shape)
            self.member_definition(node, bare)
            if mode is not None and holds_loan(shape.type):
                # The write fills the member's entries under the object around it.
                self.held_name(node, root)
        elif shape.cls is MIRClass.OPTIONAL:
            # The payload record is stored inline: copied, destroyed and
            # replaced with the field, so its verified definition comes along.
            self.record_layout(node, optional_record_field(fact.type))
            admitted(shape)
        elif shape.cls is MIRClass.VIEW:
            # A view member stores a loan in the record object, keyed by a
            # member of the record's layout: read whole, or rebound whole
            # (`view_member_write`).
            admitted(shape)
            self.held_layout(node, record)
            self.held_name(node, root)
        elif shape.cls is MIRClass.OWNED_LEAF:
            # The field is a place of the record's storage: its buffer is
            # borrowed, copied out or replaced in place, never projected into.
            admitted(shape)
            self.leaf_layout(node, fact.type)
        else:
            # A readonly-wrapped owned leaf has no field place of its own type.
            _require(node, owned_value_type(fact.type) is None, "owned-leaf record field")
            _require(node, shape.cls is MIRClass.LEAF, "unsupported field type or form")
            admitted(shape)
        f = self.fragment()
        raises = base.value.raises
        base_place = f.splice(base)
        f.value = MIRValue(MIRPlace(base_place.root, base_place.projections + f.storage_steps(base_place)
                                    + (member_field,) + ((OPTIONAL_PAYLOAD,) if payload else ())),
                           loc=loc, raises=raises)
        return shape, f

    def temporary(self, expr: th.THIRCtorCall | th.THIRCall | th.THIRMethodCall
                  ) -> tuple[th.THIROwnedRecord, Fragment]:
        """A record rvalue in storage of its full expression, read in place:
        a construct, or a call's owned record result; its backing is
        published under the expression."""
        fact = th.record_rvalue_storage(expr)
        _require(expr, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
        _require(expr, isinstance(fact, th.THIROwnedRecord) and fact.type == expr.result_type
                 and fact.readonly is False, "missing or invalid full-expression storage")
        value = self.aggregate_value(expr, fact.type, temporary=True, call=not isinstance(expr, th.THIRCtorCall))
        f = self.fragment()
        built = value.value
        f.splice(value)
        storage = f.materialize(built, expr)
        f.publish(expr, MIRPlace(storage))
        f.value = MIRValue(MIRPlace(storage), loc=expr.loc)
        return fact, f

    def full_expression(self, expr: th.THIRExpr | th.THIRPrint, *, discard: bool = False,
                        expected: TpyType | None = None,
                        check: Callable[[], Fragment] | None = None, at: object = None) -> Fragment:
        """Check `expr` as one full expression (`check`, else a scalar
        value), recording whether it owns temporaries; a discarded call is
        the statement at `at`."""
        assert self.mir.active_temporaries is None
        with self.mir.temporaries() as own:
            with self.argument_order(expr):
                if check is not None:
                    body = check()
                elif discard and (isinstance(expr, th.THIRCtorCall) or th.record_rvalue_storage(expr) is not None):
                    body = self.temporary(expr)[1]
                elif discard and _user_call(expr) and isinstance(expr.result_type, VoidType):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    body = self.call_statement(self.call(expr), at)
                elif (discard and isinstance(expr, th.THIRMethodCall) and expr.stub_callee is not None
                      and isinstance(expr.result_type, VoidType)):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    body = self.call_statement(self.method_call(expr), at)
                else:
                    body = self.expr(expr, expected)
            return self.whole(body, expr.loc, bool(own))

    def whole(self, body: Fragment, loc: object, needs_region: bool = False) -> Fragment:
        """`body` as one full expression at `loc`; `needs_region`: it owns
        temporaries, which a region of its own ends."""
        assert needs_region or not body.holds_region_storage(), "region storage without a region need"
        body.expression_loc, body.needs_region = loc, needs_region
        return body

    def call_statement(self, call: Fragment, loc: object) -> Fragment:
        """A call whose result is discarded (a void call)."""
        f = self.fragment()
        f.append(MIRCallStmt(f.splice(call), loc))
        return f

    def value_type(self, expr: th.THIRExpr, expected: TpyType | None = None) -> TpyType:
        """The leaf type a value expression has in MIR (`expr`'s admission);
        `expected` is the one its context converts it to, which only an
        unresolved number literal adopts."""
        if isinstance(expr, th.THIRMove) and expr.form is th.Form.STORAGE:
            # A last use moves into storage form (a yield's); the move arm
            # admits it only for an inert leaf, its operand's admission.
            typ = self.types[expr] = self.value_type(expr.value, expected)
            _require(expr, expr.result_type == typ, "move type mismatch")
            return typ
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        _require(expr, storage_leaf(typ), "unsupported expression type")
        _require(expr, self.types.get(expr, typ) == typ, "literal adopts two types")
        self.types[expr] = typ
        return typ

    def scalar_name_place(self, expr: th.THIRName | th.THIRModuleVar) -> MIRPlace:
        """The place a scalar name denotes, admitted as `expr` admits it: a
        global, a narrowed Optional's payload, a local's slot. A read and an
        assignment target name it alike."""
        self.scalar_name(expr, self.value_type(expr))
        return self.place(expr).value.value

    def scalar_name(self, expr: th.THIRName | th.THIRModuleVar, typ: TpyType) -> None:
        """Admit a scalar name of type `typ` (a global's admission is its place's)."""
        if place_kind(expr) is _Place.GLOBAL:
            return
        if self.wraps(expr.name, MIRClass.OPTIONAL):
            _require(expr, self.optional_name(expr, extract=True).payload == typ,
                     "optional scalar extraction required")
            return
        _plain(expr, {"name", "is_last_use", "is_movable"})
        _require(expr, expr.name in self.scope, "non-local name")
        _require(expr, self.bound(expr.name).type == typ, "name type mismatch")

    def expr(self, expr: th.THIRExpr, expected: TpyType | None = None) -> Fragment:
        """Check a value expression, as the slot its value is evaluated
        into (`value_type`)."""
        typ = self.value_type(expr, expected)
        writing = False
        loc = expr.loc
        f = self.fragment()
        match expr:
            case _ if (kind := self.admitted_place(expr, _SCALAR_PLACES)) is not None:
                if kind in (_Place.GLOBAL, _Place.NAME):
                    self.scalar_name(expr, typ)
                place = self.place(expr)
                if (reason := _SCALAR_REFUSALS.get(kind)) is not None:
                    found = place.shape_of()
                    _require(expr, found.cls is MIRClass.LEAF and found.type == typ, reason)
                if kind is _Place.FIELD:
                    # A temporary receiver's evaluation may write (a call's
                    # arguments); a place receiver evaluates nothing.
                    writing = self.writes.get(expr.receiver, False)
                elif kind is _Place.ELEMENT:
                    writing = self.writes[expr.index]
                # A read through an element subscript may raise when its index is unproven.
                raises = place.value.raises
                result = f.leaf(typ, MIRRead(f.splice(place), raises), loc)
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                value = converted_literal(typ, expr.value)
                _require(expr, value is not None, "unsupported literal value")
                result = f.leaf(typ, MIRConstant(value), loc)
            case th.THIRCharLiteral():
                _plain(expr, {"value"})
                _require(expr, leaf_constant(typ, expr.value), "unsupported literal value")
                result = f.leaf(typ, MIRConstant(expr.value), loc)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                call = self.call(expr)
                result = f.leaf(typ, f.splice(call), loc)
                writing = self.call_writes(expr)
            case th.THIRIsinstance():
                _plain(expr, {"subject", "member_cpps", "wrapper", "union_test"})
                fact = expr.union_test
                # The test reads the slot its fact names; a subject with no
                # slot (a keyless operand) carries no fact.
                _require(expr, typ == BOOL and isinstance(fact, th.THIRUnionTest)
                         and isinstance(expr.subject, th.THIRName)
                         and expr.subject.name == fact.source
                         and self.union_is(fact.source, fact.layout)
                         and bool(fact.alternatives) and len(set(fact.alternatives)) == len(fact.alternatives)
                         and all(type(i) is int and 0 <= i < len(fact.layout.elements)
                                 for i in fact.alternatives), "missing or invalid union test")
                result = self.is_alternative(f, MIRPlace(self.slot_of(fact.source)), fact.alternatives, True, loc)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                call = self.method_call(expr)
                result = f.leaf(typ, f.splice(call), loc)
                writing = self.call_writes(expr)
            case th.THIRSubscript() if expr.tuple_index is None:
                _plain(expr, {"receiver", "index", "bounds_safe", "bound_getitem", "element_access",
                              "unchecked_getitem"})
                _require(expr, expr.certified_op, "uncertified element read")
                receiver = self.operand(expr.receiver, self.lent_value(expr.receiver))
                _require(expr, owned_leaf(self.lent_type(expr.receiver, receiver)), "element read needs an owned leaf")
                index = self.value(expr.index)
                # An element read of an owned leaf may raise (an index out of range).
                result = f.leaf(typ, MIROp("getitem", (f.splice(receiver), f.splice(index)), may_raise=True), loc)
                writing = self.writes[expr.index]
            case th.THIRIsNone() if expr.union_monostate:
                _plain(expr, {"operand", "negate", "union_monostate"})
                _require(expr, isinstance(expr.operand, th.THIRName) and typ == BOOL
                         and type(expr.negate) is bool, "unsupported union None test")
                layout = self.union_name(expr.operand)
                _require(expr, None in layout.elements, "union has no absence alternative")
                result = self.is_alternative(f, MIRPlace(self.slot_of(expr.operand.name)),
                                             (layout.elements.index(None),), not expr.negate, loc)
            case th.THIRIsNone() if isinstance(expr.operand, th.THIRFieldAccess):
                _plain(expr, {"operand", "negate", "value_repr"})
                # An Optional record field tested in place: `std::optional` storage.
                whole, place = self.field(expr.operand)
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and whole.cls is MIRClass.OPTIONAL and expr.value_repr is True,
                         "unsupported optional field test")
                result = self.is_alternative(f, f.splice(place), (1,), expr.negate, loc)
            case th.THIRIsNone():
                _plain(expr, {"operand", "negate", "value_repr"})
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and isinstance(expr.operand, th.THIRName), "unsupported optional test")
                layout = self.optional_name(expr.operand, extract=False)
                _require(expr, expr.value_repr is (not isinstance(layout.payload, th.THIRBorrowedRecord)),
                         "optional test representation mismatch")
                result = self.is_alternative(f, MIRPlace(self.slot_of(expr.operand.name)), (1,), expr.negate, loc)
            case th.THIRCoerce() if expr.certified_conversion:
                _plain(expr, {"expr", "coercion_name", "wrap"})
                operand = self.value(expr.expr)
                result = f.leaf(typ, MIROp(f"coerce:{expr.coercion_name}", (f.splice(operand),), may_raise=True), loc)
                writing = self.writes[expr.expr]
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS
                         and isinstance(expr.expr, th.THIRLiteral), "unsupported coercion")
                _require(expr, self.expr(expr.expr, typ).shape_of().type == typ, "unsupported literal coercion")
                # The literal renders directly in the slot's type.
                result = f.leaf(typ, MIRConstant(expr.expr.value), loc)
            case th.THIRWalrus():
                _require(expr, expr.name not in self.range_counters, "range target write needs separate induction")
                _plain(expr, {"name", "cpp_name", "value", "global_binding"})
                if expr.global_binding is not None:
                    self.global_binding(expr, write=True)
                else:
                    _require(expr, self.bound(expr.name) is not None and self.bound(expr.name).type == typ,
                             "walrus needs existing scalar local")
                before = len(self.mir.active_temporaries or ())
                value = self.expr(expr.value, typ)
                _require(expr, value.shape_of().type == typ, "walrus type mismatch")
                _require(expr, expr.global_binding is None or len(self.mir.active_temporaries or ()) == before,
                         "temporary global assignment")
                writing = True
                result = f.splice(value)
                target = (self.global_place(expr) if expr.global_binding is not None
                          else MIRPlace(self.slot_of(expr.name)))
                f.write(target, MIRRead(MIRPlace(result)), loc)
            case th.THIRUnaryNot():
                _plain(expr, {"operand"})
                _require(expr, typ == BOOL, "not requires bool")
                operand = self.expr(expr.operand)
                _require(expr, operand.shape_of().type == BOOL, "not requires bool")
                writing = self.writes[expr.operand]
                result = f.leaf(BOOL, MIRNot(f.splice(operand)), loc)
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                if expr.op in ("&&", "||"):
                    left, right = self.expr(expr.left), self.expr(expr.right)
                    _require(expr, typ == BOOL and left.shape_of().type == BOOL and right.shape_of().type == BOOL
                             and expr.resolved is None, "unsupported boolean operation")
                    result = self.logical(f, expr.op, left, self.anchored(expr.right, right), loc)
                else:
                    _require(expr, expr.certified_op, "uncertified binary operation")
                    left, right = self.promotion(expr, *self.operands(expr))
                    if ((self.writes[expr.left] and not _literal(expr.right))
                            or (self.writes[expr.right] and not _literal(expr.left))):
                        raise MIRUnsupported(expr, "order-sensitive eager operands")
                    _require(expr, expr.op not in th.COMPARISON_OPS or typ == BOOL and certified_primitive_comparison(
                        left.shape_of().type, right.shape_of().type), "comparison operand type mismatch")
                    operands = (f.splice(left), f.splice(right))
                    # A primitive operation's contract admits raising.
                    result = (f.leaf(BOOL, MIRCompare(expr.op, *operands), loc) if expr.op in th.COMPARISON_OPS
                              else f.leaf(typ, MIROp(expr.op, operands, may_raise=True), loc))
                writing = self.writes[expr.left] or self.writes[expr.right]
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                operand = self.value(expr.operand)
                result = f.leaf(typ, MIROp(expr.resolved.method.name, (f.splice(operand),), may_raise=True), loc)
                writing = self.writes[expr.operand]
            case th.THIRValueSelect():
                _plain(expr, {"lhs", "rhs", "op", "lhs_temp_cpp"})
                _require(expr, expr.op in ("&&", "||") and typ == BOOL, "unsupported value select")
                before = len(self.mir.active_temporaries or ())
                lhs = self.expr(expr.lhs)
                _require(expr, lhs.shape_of().type == BOOL, "unsupported value select")
                # A hoisted LHS has its own full expression and may extend a subobject's lifetime.
                _require(expr, expr.lhs_temp_cpp is None or len(self.mir.active_temporaries or ()) == before,
                         "temporary in hoisted select operand")
                rhs = self.expr(expr.rhs)
                _require(expr, rhs.shape_of().type == BOOL,
                         "unsupported value select")
                # A pure bool select never needs a representation-changing temp.
                _require(expr, expr.lhs_temp_cpp in (None, "auto&&"), "unsupported select temporary")
                writing = self.writes[expr.lhs] or self.writes[expr.rhs]
                result = self.logical(f, expr.op, lhs, self.anchored(expr.rhs, rhs), loc)
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                cond = self.expr(expr.cond)
                _require(expr, cond.shape_of().type == BOOL, "condition requires bool")
                then = self.expr(expr.then, typ)
                _require(expr, then.shape_of().type == typ
                         and (orelse := self.expr(expr.orelse, typ)).shape_of().type == typ,
                         "conditional arm type mismatch")
                writing = any(self.writes[e] for e in (expr.cond, expr.then, expr.orelse))
                result = f.select(f.splice(cond), self.anchored(expr.then, then),
                                  self.anchored(expr.orelse, orelse), typ, loc)
            case th.THIRMove() if storage_leaf(typ):
                # Moving an inert leaf copies it: its operand read.
                _plain(expr, {"value"})
                operand = self.expr(expr.value, typ)
                _require(expr, operand.shape_of().type == typ, "move type mismatch")
                writing = self.writes[expr.value]
                result = f.splice(operand)
            case _:
                raise MIRUnsupported(expr, "unsupported expression")
        f.value = MIRValue(result, loc=loc)
        self.writes[expr] = writing
        return f

    def is_alternative(self, f: Fragment, place: MIRPlace, alternatives: tuple[int, ...], holds: bool,
                       loc: object) -> MIRSlotId:
        """Whether the sum at `place` selects one of `alternatives` (`holds`),
        or selects none of them."""
        found = f.leaf(BOOL, MIRIsAlternative(place, alternatives), loc)
        return found if holds else f.leaf(BOOL, MIRNot(found), loc)

    def logical(self, f: Fragment, op: str, left: Fragment, right: Fragment, loc: object) -> MIRSlotId:
        """`a && b` / `a || b`: the right operand evaluates only when it decides."""
        value = f.splice(left)
        return f.select(value, right if op == "&&" else value, value if op == "&&" else right, BOOL, loc)

    def lent_type(self, expr: th.THIRExpr, plan: Fragment) -> TpyType:
        """The owned leaf a lent operand (`lent_value`) reads: a view's own leaf, else its type."""
        return view_owned_leaf(expr.result_type) if view_leaf(expr.result_type) else plan.shape_of().type

    def promotion(self, expr: th.THIRBinOp, left: Fragment, right: Fragment) -> tuple[Fragment, Fragment]:
        """The operands a certified operator reads: a promoted operand
        (`THIRBinOp.promoted_operand`) is converted into a fresh value of
        the promotion's type, an owned one in a temporary of the full
        expression."""
        side = expr.promoted_operand
        if side is None:
            return left, right
        promotion = expr.resolved.promotion
        target = promotion.return_type
        owned = owned_leaf(target)
        if owned:
            _require(expr, self.mir.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.leaf_layout(expr, target)
        operand = (expr.left, expr.right)[side]
        promoted = self.fragment()
        conversion = MIROp(f"coerce:{promotion.name}", (promoted.splice((left, right)[side]),), may_raise=True)
        if owned:
            temporary = promoted.materialize(
                MIRValue(conversion, owned_leaf_shape(target, MIRRole.STORAGE), operand.loc), operand)
            result = promoted.holder(owned_leaf_shape(target, MIRRole.LOAN), MIRBorrow(MIRPlace(temporary)),
                                     operand.loc)
        else:
            result = promoted.leaf(target, conversion, operand.loc)
        promoted.value = MIRValue(result, loc=operand.loc)
        return (promoted, right) if side == 0 else (left, promoted)

    def operands(self, expr: th.THIRBinOp) -> tuple[Fragment, Fragment]:
        """Check both operands. A number literal converts into the type at
        its own position of the resolved dunder (`1.0 + i` binds the float
        receiver, not i's int32); under a derived comparison, to the other
        operand's type."""
        left, right = expr.left, expr.right
        if isinstance(left, th.THIRLiteral) and not isinstance(right, th.THIRLiteral):
            right_plan = self.value(right)
            return self.value(left, expr.operand_position_type(0) or right_plan.shape_of().type), right_plan
        left_plan = self.value(left)
        position = expr.operand_position_type(1) if isinstance(right, th.THIRLiteral) else None
        return left_plan, self.value(right, position or left_plan.shape_of().type)

    def stmt(self, stmt: th.THIRStmt, loops: int) -> object:
        match stmt:
            case th.THIRAssign(target=th.THIRName(name=name)) | th.THIRPtrLocalRebind(name=name):
                _require(stmt, name not in self.iteration_references,
                         "iteration source or reference target cannot be reseated")
                _require(stmt, name not in self.range_counters, "range target write needs separate induction")
                _require(stmt, name not in self.tuple_roots, "owned tuple binding replacement is unsupported")
            case th.THIRNarrowAlias(alias=name):
                _require(stmt, name not in self.range_counters, "range target write needs separate induction")
                _require(stmt, name not in self.tuple_roots, "owned tuple binding replacement is unsupported")
        match stmt:
            case th.THIRNarrowAlias():
                _plain(stmt, {"alias", "variant_cpp", "member_cpp", "is_ptr_variant", "const_ref", "union_extraction"})
                member = self.union_extraction(stmt, stmt.union_extraction)
                reference = isinstance(member, th.THIRBorrowedRecord)
                _require(stmt, stmt.is_ptr_variant is reference, "union alias form mismatch")
                fact = stmt.union_extraction
                source = MIRPlace(self.slot_of(fact.source), (MIRUnionPayload(fact.alternative),))
                # A scalar payload is aliased in place, readonly: the binding is the payload itself.
                shape = (_loan(member) if reference else value_shape(
                    member, MIRClass.LEAF, access=loan_access(member, MIRClass.LEAF, Local(alias=True)),
                    role=MIRRole.ALIAS))
                bound = self.bind(stmt.alias, shape, loc=stmt.loc,
                                  alias_source=source if shape.holding is MIRHolding.ALIAS else None)
                return self.put_narrow_alias(stmt.loc, bound.slot, source)
            case th.THIRAssign() if stmt.optional_record_assignment is not None:
                _plain(stmt, {"target", "value", "optional_record_assignment"})
                _require(stmt, isinstance(stmt.target, th.THIRName), "optional record assignment needs local")
                _plain(stmt.target, {"name"})
                fact = stmt.optional_record_assignment
                name = stmt.target.name
                bound = self.scope.get(name)
                backing = self.mir.records[bound.backing] if bound is not None and bound.backing is not None else None
                _require(stmt, backing is not None and backing.shape.owned_sum
                         and self.holds(name, fact)
                         and self.bound(name).type == stmt.target.result_type == fact.type,
                         "optional record backing mismatch")
                value = self.site_fill(stmt.value, fact.type)
                _require(stmt, self.movable(fact.type), "optional backing needs movable record")
                return self.put_replacement(stmt.loc, bound.slot, value, bound.backing)
            # A union or Optional reseat: the destination binding's class picks
            # the operation, as a declaration's laid-out class does (a name
            # bound as neither is the Optional's to refuse); its fact must lay
            # that binding out (`union_is`, `optional_is`).
            case th.THIRAssign() | th.THIRPtrLocalRebind() if layout_fact(stmt) is not None:
                name = (stmt.name if isinstance(stmt, th.THIRPtrLocalRebind)
                        else stmt.target.name if isinstance(stmt.target, th.THIRName) else None)
                if name is None or not self.wraps(name, MIRClass.UNION):
                    return self.optional_assignment(stmt)
                _require(stmt, isinstance(stmt, th.THIRAssign), "unsupported union reseat")
                _plain(stmt, {"target", "value", "union_layout", "union_literal"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                _require(stmt, name not in self.parameters and self.union_is(name, stmt.union_layout),
                         "union destination mismatch")
                return self.put_union_assign(stmt.loc, self.slot_of(name),
                                    self.union_source(stmt.value, self.bound(name), stmt.union_literal,
                                                      stmt.union_layout),
                                    self.bound(name).scalar_wrapper)
            case th.THIRAssign() if self.replaces(stmt):
                return self.replacement(stmt)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if self.rebinds_holder(stmt):
                return self.alias_local(stmt)
            case th.THIRNoOpStmt():
                _plain(stmt, set())
                return None
            case th.THIRSetItem():
                return self.element_write(stmt)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.container_view_access(stmt.target.name) is not None):
                # Reseating a Span holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                checked = self.full_expression(stmt.value,
                                               check=lambda: self.span_value(stmt.value, self.bound(name).type))
                readonly = checked.shape_of().readonly
                _require(stmt, not readonly or self.container_view_access(name), "alias increases access")
                return self.put_assign(stmt.loc, self.name_place(name, stmt.target.loc), checked, None)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.view_bound(stmt.target.name)):
                # Reseating a view holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                return self.put_assign(stmt.loc, self.name_place(name, stmt.target.loc), self.full_expression(
                    stmt.value, check=lambda: self.view_value(stmt.value, self.bound(name).type)), None)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName)
                                     and owned_value_type(stmt.target.result_type) is not None):
                _plain(stmt, {"target", "value"})
                target = stmt.target
                if target.global_binding is not None:
                    self.global_binding(target, write=True)
                _plain(target, {"name", "is_last_use", "is_movable"})
                _require(stmt, self.owned_leaf_bound(target.name) is True
                         and self.bound(target.name).type == target.result_type,
                         "owned-leaf assignment needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.owned_sink(stmt.value))
                _require(stmt, value.shape_of().type == target.result_type, "assignment type mismatch")
                # Replaces the buffer in place.
                return self.put_assign(stmt.loc, self.name_place(target.name, target.loc), value,
                                       MIRStorageWriteMode.IN_PLACE)
            case th.THIRAssign() if isinstance(stmt.target, th.THIRFieldAccess):
                _plain(stmt, {"target", "value"})
                shape, place = self.field(stmt.target, write=True)
                return self.field_write(stmt, stmt.loc, shape, place, stmt.value, MIRStorageWriteMode.IN_PLACE)
            case th.THIRAssign():
                _plain(stmt, {"target", "value"})
                if isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None:
                    self.global_binding(stmt.target, write=True)
                if isinstance(stmt.target, th.THIRName) and self.wraps(stmt.target.name, MIRClass.TUPLE):
                    _require(stmt, stmt.target.name not in self.parameters, "tuple parameter reseat")
                    _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                    _require(stmt, stmt.target.result_type == self.bound(stmt.target.name).type,
                             "tuple destination type mismatch")
                    value = self.tuple_expr(stmt.value)
                    self.tuple_compatible(stmt, value.shape_of().members, self.bound(stmt.target.name).members)
                    return self.put_assign(stmt.loc, self.name_place(stmt.target.name, stmt.target.loc),
                                           self.tuple_copy(value, stmt.target.name), None)
                _require(stmt, isinstance(stmt.target, th.THIRName), "assignment needs local or field target")
                _require(stmt, not self.payload_alias(stmt.target.name), "write through scalar payload alias")
                place = self.valued(MIRValue(self.scalar_name_place(stmt.target), loc=stmt.target.loc))
                target_type = self.types[stmt.target]
                value = self.full_expression(stmt.value, expected=target_type)
                _require(stmt, not (stmt.target.global_binding is not None and value.needs_region),
                         "temporary global assignment")
                _require(stmt, target_type == value.shape_of().type, "assignment type mismatch")
                return self.put_scalar_assign(stmt.loc, place, value)
            case th.THIRExprStmt():
                _plain(stmt, {"expr", "void_cast"})
                return self.put_expression(stmt.loc, self.full_expression(stmt.expr, discard=True, at=stmt.loc))
            case th.THIRReturn():
                return self.return_(stmt)
            case th.THIRResumableReturn():
                # A generator's `return` ends it without a value.
                _plain(stmt, {"has_operand"})
                _require(stmt, self.frame is not None, "generator return value")
                f = self.fragment()
                f.end(MIRReturn(None, stmt.loc))
                return f
            case th.THIRForRange():
                return self.range_loop(stmt, loops)
            case th.THIRForEach():
                return self.native_loop(stmt, loops)
            case th.THIRIf():
                return self.if_(stmt, loops)
            case th.THIRWhile():
                _plain(stmt, {"condition", "body", "orelse", "hoist_decls", "hoisted_bindings"})
                hoists = self.hoists(stmt)
                # A condition with temporaries is evaluated per iteration, in a
                # temporary plan scope of its own the body shares.
                iteration = self.temp_scope(stmt, "iteration") if self.planned else None
                else_scope = self.temp_scope(stmt, "else") if stmt.orelse else None
                f = self.fragment()
                # A `continue` re-runs the condition: through a bridge when it is
                # evaluated per iteration.
                head, then, normal, after = f.block(), f.block(), f.block(), f.block()
                with self.temp_in(iteration):
                    condition = self.full_expression(stmt.condition)
                    _require(stmt, condition.shape_of().type == BOOL, "condition requires bool")
                    with self.temp_in(self.temp_scope(stmt, "loop") if iteration is None else None):
                        self.loops.append((head.id, after.id))
                        body = self.scoped(stmt.body, loops + 1)
                        self.loops.pop()
                    temporaries = self.statement_temporaries(stmt) if iteration is not None else None
                with self.temp_in(else_scope):
                    orelse = self.scoped(stmt.orelse, loops)
                loc = stmt.loc
                self.place_hoists(f, hoists, loc)
                if iteration is not None:
                    bridge, entry = head, then
                    f.end(MIRGoto(bridge.id, loc))
                    f.current = bridge
                    f.end(MIRGoto(entry.id, loc))
                    f.current = entry
                    with f.scope():
                        if temporaries is not None:
                            f.splice(temporaries)
                        value = f.full_expression_value(condition)
                        block = f.block()
                        f.branch(value, block.id, normal.id, loc)
                        f.current = block
                        self.sequence(f, body)
                        if f.current is not None:
                            f.end(MIRGoto(bridge.id, loc, _last_loc(stmt.body)))
                    f.current = normal
                    if stmt.orelse:
                        self.scoped_into(f, orelse)
                else:
                    f.end(MIRGoto(head.id, loc))
                    f.current = head
                    f.branch(f.full_expression_value(condition), then.id, normal.id, loc)
                    f.current = then
                    self.scoped_into(f, body)
                    if f.current is not None:
                        f.end(MIRGoto(head.id, loc, _last_loc(stmt.body)))
                    f.current = normal
                    self.scoped_into(f, orelse)
                if f.current is not None:
                    f.end(MIRGoto(after.id, loc, _last_loc(stmt.orelse)))
                f.current = after
                return f
            case th.THIRBreak() | th.THIRContinue():
                _plain(stmt, set())
                _require(stmt, loops > 0, "loop control outside loop")
                return self.put_jump(stmt.loc, isinstance(stmt, th.THIRContinue))
            case th.THIRParamCopy():
                # The body's owned copy of a borrowed parameter it reassigns; reads see the copy.
                _plain(stmt, {"name", "cpp_type", "init_cpp"})
                _require(stmt, any(s is stmt for s in self.top_statements) and stmt.name in self.parameters
                         and self.owned_leaf_bound(stmt.name) is False, "param copy needs a borrowed owned-leaf parameter")
                typ = self.bound(stmt.name).type
                parameter = self.name_place(stmt.name, stmt.loc)
                shape = value_shape(typ)
                bound = self.bind(stmt.name, shape, loc=stmt.loc)
                return self.put_param_copy(stmt.loc, bound.slot, parameter.sink(shape))
            case th.THIRStrAppend() if stmt.target_expr is not None:
                # A field append: THIR carries the field lvalue, which the emit prefers to `target`.
                _plain(stmt, {"target", "value", "target_expr"})
                _require(stmt, isinstance(stmt.target_expr, th.THIRFieldAccess), "append needs a field place")
                shape, place = self.field(stmt.target_expr, write=True)
                _require(stmt, primitive_owned_leaf(shape.type) and shape.cls is MIRClass.OWNED_LEAF,
                         "append needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
                # Python reads the field before the value runs; C++ appends to
                # whatever the value left there.
                _require(stmt, not self.writes[stmt.value], "order-sensitive eager operands")
                return self.put_append(stmt.loc, place, stmt.target_expr.result_type, value)
            case th.THIRStrAppend():
                _plain(stmt, {"target", "value"})
                _require(stmt, self.owned_leaf_bound(stmt.target) is True
                         and primitive_owned_leaf(self.bound(stmt.target).type), "append needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
                return self.put_append(stmt.loc, self.name_place(stmt.target, stmt.loc), self.bound(stmt.target).type, value)
            case th.THIRPrint():
                _plain(stmt, {"args", "sep_value", "end_value"})
                _require(stmt, all(v is None or isinstance(v, str) for v in (stmt.sep_value, stmt.end_value)),
                         "print separator needs a literal")
                # The print call is one full expression: its arguments' temporaries
                # (a call's owned-leaf result, a built operand) live until it returns.
                return self.put_expression(stmt.loc, self.full_expression(
                    stmt, check=lambda: self.print_arguments(stmt)))
        raise MIRUnsupported(stmt, "unsupported statement")

    def if_(self, stmt: th.THIRIf, loops: int) -> Fragment:
        """An if; under a temporary plan an elif chain is one statement, each
        continuing condition in the else block of the one before."""
        arms, orelse, has_else = self.if_arms(stmt, loops)
        f = self.fragment()
        if self.planned:
            self.planned_if(f, arms, orelse, has_else)
            return f
        (loc, hoists, _, condition, then, _, _), = arms
        self.place_hoists(f, hoists, loc)
        cond = f.full_expression_value(condition)
        yes, no = f.block(), f.block()
        f.branch(cond, yes.id, no.id, loc)
        exits = []
        for block, arm, body in ((yes, then, stmt.then_body), (no, orelse, stmt.else_body)):
            f.current = block
            self.scoped_into(f, arm)
            if f.current is not None:
                exits.append((f.current, _last_loc(body)))
        if exits:
            join = f.block()
            for block, last in exits:
                f.current = block
                f.end(MIRGoto(join.id, loc, last))
            f.current = join
        else:
            f.current = None
        return f

    def if_arms(self, stmt: th.THIRIf, loops: int) -> tuple[list[tuple], list[Fragment], bool]:
        """An If's arms -- under a temporary plan, its elif chain's, each in
        its condition's scope -- the final else body, and whether there is one."""
        _plain(stmt, {"condition", "then_body", "else_body", "else_is_nested", "hoist_decls",
                      "hoisted_bindings"})
        hoists = self.hoists(stmt)
        condition = self.full_expression(stmt.condition)
        _require(stmt, condition.shape_of().type == BOOL, "condition requires bool")
        elif_ = len(if_chain(stmt)) > 1
        with self.temp_in(self.temp_scope(stmt, "then")):
            then = self.scoped(stmt.then_body, loops)
        if self.planned and elif_:
            following = stmt.else_body[0]
            with self.nested(), self.temp_in(self.temp_scope(following, "condition")):
                rest, orelse, has_else = self.if_arms(following, loops)
        else:
            with self.temp_in(self.temp_scope(stmt, "else") if stmt.else_body and not elif_ else None):
                orelse = self.scoped(stmt.else_body, loops)
            rest, has_else = [], bool(stmt.else_body)
        arm = (stmt.loc, hoists, self.statement_temporaries(stmt), condition, then,
               self.temp_scope(stmt, "condition"), stmt)
        return [arm, *rest], orelse, has_else

    def planned_if(self, f: Fragment, arms: list[tuple], orelse: list[Fragment], has_else: bool) -> None:
        self.place_hoists(f, arms[0][1], arms[0][0])
        join = f.block()

        def arm(index: int) -> None:
            loc, hoists, temporaries, condition, then, _, node = arms[index]
            if index:
                # An elif is an If of its own, whose hoists are planned too.
                self.place_hoists(f, hoists, loc)
            if temporaries is not None:
                f.splice(temporaries)
            value = f.full_expression_value(condition)
            yes, no = f.block(), f.block()
            f.branch(value, yes.id, no.id, loc)
            f.current = yes
            self.scoped_into(f, then)
            if f.current is not None:
                f.end(MIRGoto(join.id, loc, _last_loc(node.then_body)))
            f.current = no
            if index + 1 < len(arms):
                # A continuing condition with temporaries opens a scope of its own.
                if arms[index + 1][5] is not None:
                    with f.scope():
                        arm(index + 1)
                else:
                    arm(index + 1)
            else:
                # The plan gives a non-empty final else body its scope.
                if has_else:
                    self.scoped_into(f, orelse)
                if f.current is not None:
                    f.end(MIRGoto(join.id, loc, _last_loc(node.else_body) if has_else else None))

        arm(0)
        f.current = join

    def optional_assignment(self, stmt: th.THIRAssign | th.THIRPtrLocalRebind) -> Fragment:
        """An Optional local's value replaced: from its source, or a record
        built into new backing (OWN) or into its payload in place (IN_PLACE)."""
        if isinstance(stmt, th.THIRAssign):
            _plain(stmt, {"target", "value", "optional_layout", "rebind_storage", "slot_cpp"})
            _require(stmt, isinstance(stmt.target, th.THIRName), "optional assignment needs local")
            _plain(stmt.target, {"name", "is_last_use", "is_movable"})
            name = stmt.target.name
        else:
            _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE, "unsupported optional reseat")
            _plain(stmt, {"name", "kind", "optional_layout"})
            name = stmt.name
        _require(stmt, name not in self.parameters and self.optional_is(name, stmt.optional_layout),
                 "optional destination mismatch")
        if isinstance(stmt, th.THIRAssign):
            member = stmt.optional_layout.payload
            typ = member.type if isinstance(member, th.THIRBorrowedRecord) else member
            actual = unwrap_readonly(stmt.target.result_type)
            _require(stmt, actual in (typ, self.bound(name).type) or isinstance(actual, NoneType),
                     "optional destination type mismatch")
        if isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is not None:
            member = stmt.optional_layout.payload
            _require(stmt, isinstance(member, th.THIRBorrowedRecord)
                     and stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                     "unsupported optional replacement storage")
            _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE or not member.readonly,
                     "readonly in-place replacement")
            own = stmt.rebind_storage is RebindStorage.OWN
            value = self.site_fill(stmt.value, member.type) if own else self.aggregate_value(stmt.value, member.type)
            _require(stmt, self.movable(member.type), "replacement needs movable record")
            backing = self.site_backing(stmt, member.type) if own else None
            return self.put_optional_assign(stmt.loc, self.slot_of(name), value, backing, not own,
                                   self.bound(name).scalar_wrapper)
        _require(stmt, not isinstance(stmt, th.THIRAssign) or stmt.slot_cpp is None,
                 "optional slot without replacement storage")
        value = self.optional_source(stmt.value, self.bound(name))
        return self.put_optional_assign(stmt.loc, self.slot_of(name), value, None, False,
                                        self.bound(name).scalar_wrapper)

    def return_(self, stmt: th.THIRReturn) -> Fragment:
        """A return, built whole: its value in the form the body's result
        takes, then the return; nothing after it in its block runs."""
        _plain(stmt, {"value"})
        loc = stmt.loc
        f = self.fragment()
        if stmt.value is None and self.constructor_result is not None:
            # A constructor's `return` returns the record it built.
            f.end(MIRReturn(self.constructor_result, loc))
            return f
        if stmt.value is None:
            _require(stmt, isinstance(self.return_type, VoidType), "missing return value")
            f.end(MIRReturn(None, loc))
            return f
        result = self.result
        if result is not None and view_leaf(result.type):
            value = self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, result.type))
            return self.holder_return(f, view_holder_shape(result.type), value, loc)
        if result is not None and region_view(result.type):
            value = self.full_expression(stmt.value, check=lambda: self.span_value(stmt.value, result.type))
            _require(stmt, not value.shape_of().readonly or result.readonly, "return increases access")
            return self.holder_return(f, _loan(result), value, loc)
        if result is not None:
            value = f.splice(self.borrowed_expression(stmt.value, _loan(result)))
            # The return a borrow operation is published under.
            f.borrow_operation(stmt)
            f.end(MIRReturn(value, loc))
            return f
        moved: list[MIRSlotId | None] = [None]
        if (typ := self.owned_aggregate_result) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_aggregate_source(stmt.value, typ, moved))
            return self.owned_return(f, value, value_shape(typ), moved[0], loc)
        if (owned := owned_value_type(self.return_type)) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_leaf_source(stmt.value, moved))
            _require(stmt, value.shape_of().type == owned, "return type mismatch")
            return self.owned_return(f, value, value_shape(owned), moved[0], loc)
        value = self.full_expression(stmt.value, expected=self.return_type)
        _require(stmt, value.shape_of().type == self.return_type, "return type mismatch")
        f.end(MIRReturn(f.full_expression_value(value), loc))
        return f

    def holder_return(self, f: Fragment, shape: MIRShape, value: Fragment, loc: object) -> Fragment:
        """A returned view or container view, written into a result holder
        that outlives the full expression, whose temporaries it must not reach."""
        holder = f.slot(shape)
        with f.full_expression(value):
            f.write(holder, f.splice(value), loc)
        f.end(MIRReturn(holder, loc))
        return f

    def owned_return(self, f: Fragment, value: Fragment, result: MIRShape, moved: MIRSlotId | None,
                     loc: object) -> Fragment:
        """An owned result: a binding's own storage (an aggregate's backing,
        an owned-leaf binding itself) leaves the body itself --
        the move is C++'s (or elided), not an event of the body; anything
        else is copied or built into a result slot."""
        if moved is not None:
            f.end(MIRReturn(moved, loc))
            return f
        dest = f.slot(result, storage_duration=f.scoped_duration())
        with f.full_expression(value):
            f.write(dest, f.splice(value), loc, MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
        f.end(MIRReturn(dest, loc))
        return f

    def owned_aggregate_source(self, expr: th.THIRExpr, typ: NominalType, moved: list) -> Fragment:
        """What an `Own[...]` record or container result takes: the backing
        of a fixed owned local moved out (C++ moves or elides it), or a
        result built for it."""
        source = expr.value if isinstance(expr, th.THIRMove) else expr
        if isinstance(source, th.THIRName) and source.global_binding is None:
            # A reseat may retarget the holder at another backing: the local's own may be stale.
            _require(expr, source.name not in self.owned_locals or source.name in self.fixed_owned,
                     "reassigned local returned")
        if isinstance(expr, th.THIRName) and expr.global_binding is None:
            _require(expr, expr.name in self.fixed_owned, "owned record return needs fixed owned local")
            _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
            # A readonly holder cannot be moved from: C++ would copy it.
            _require(expr, not self.holder(expr.name).readonly, "readonly local returned")
            _require(expr, self.holder(expr.name).type == typ, "return type mismatch")
            self.writes[expr] = False
            moved[0] = self.scope[expr.name].backing
            return self.fragment()
        return self.aggregate_value(expr, typ, call=True)

    def owned_leaf_source(self, expr: th.THIRExpr, moved: list) -> Fragment:
        """What an owned-leaf result takes: the body's own storage of a
        binding moved out (C++ moves it), or a copy or a build."""
        plan = self.owned_sink(expr)
        moved[0] = plan.value.storage
        return plan

    def element_write(self, stmt: th.THIRSetItem) -> Fragment:
        """`xs[i] = v`: replaces an element in place -- a weak update of the
        container's element region -- when the receiver's `__setitem__` stub
        declares it writes elements (`mutates="elements"`: it moves no
        element) or writes through a view, which cannot change its source's
        shape; otherwise the `__setitem__` call it is, a structure write of
        the receiver derived from the stub like any container method."""
        _plain(stmt, {"target", "value", "setter", "unchecked_setter", "stub_callee"})
        callee = stmt.stub_callee
        _require(stmt, isinstance(callee, th.THIRStubCallee) and callee.receiver, "element write needs a stub contract")
        target = stmt.target
        _require(stmt, isinstance(target, th.THIRSubscript) and self.container_receiver(target.receiver),
                 "element write needs a container place")
        if not (callee.mutates_elements is True or container_view(signature_params(callee.signature)[0])):
            return self.put_expression(stmt.loc, self.full_expression(
                stmt.value, check=lambda: self.setitem_call(stmt, callee)))

        def check() -> Fragment:
            element = self.element_access(target)
            member = element.shape_of()
            # Stricter than the write rule (`store_problem`), which lets a
            # mutable container replace a readonly element: a coverage
            # restriction (TODO.md "readonly element store").
            _require(stmt, not member.readonly, "readonly element store")
            match member.cls:
                case MIRClass.LEAF:
                    value = self.expr(stmt.value, member.type)
                    _require(stmt, value.shape_of().type == member.type, "assignment type mismatch")
                    value.sink(value.shape_of())
                case MIRClass.OWNED_LEAF:
                    value = self.owned_sink(stmt.value)
                    _require(stmt, value.shape_of().type == member.type, "assignment type mismatch")
                case _:
                    value = self.aggregate_value(stmt.value, member.type, call=True)
            self.argument_order_rule(stmt, (target.index, stmt.value))
            # `xs[i] = v`: the value in the member's form (Python evaluates
            # it first), then the index, replaces the element region in
            # place under the container's root.
            f = self.fragment()
            stored = f.splice(value)
            place = f.splice(element)
            f.write(place, stored, stmt.loc, MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, place.root))
            return f
        return self.put_expression(stmt.loc, self.full_expression(stmt.value, check=check))

    def setitem_call(self, stmt: th.THIRSetItem, callee: th.THIRStubCallee) -> Fragment:
        """A `__setitem__` whose stub may move elements, as the stub call:
        the receiver place is parameter 0 (written: a structure write by the
        stub rule), the index and the value follow the stub-call rows."""
        target = stmt.target
        summary = self.stub_summaries.get(callee.identity)
        if summary is None:
            summary = stub_summary(callee)
            if isinstance(summary, str):
                raise MIRUnsupported(stmt, summary)
            self.stub_summaries[callee.identity] = summary
        _require(stmt, summary.callee == callee and len(summary.parameters) == 3
                 and isinstance(signature_result(callee.signature), VoidType), "call signature mismatch")
        place = self.container_place(target.receiver)
        shape = place.shape_of()
        call = self.fragment()
        slots = [call.lend(place, raises=False)]
        receiver = summary.parameters[0]
        _require(stmt, receiver.type == shape.type and not shape.readonly, "readonly element store")
        for arg, binding in zip((target.index, stmt.value), summary.parameters[1:]):
            _require(arg, not binding.protocol, "unsupported element call parameter")
            slots.append(call.splice(self.argument(binding, arg)))
        self.argument_order_rule(stmt, (target.index, stmt.value))
        # No call site: `xs[i] = v` is no call expression a diagnostic could find its arguments in.
        return self.call_statement(self.call_value(call, stmt, summary, slots, shape.readonly, None), stmt.loc)

    def print_arguments(self, stmt: th.THIRPrint) -> Fragment:
        f = self.fragment()
        arguments: list[MIRSlotId] = []
        for arg in stmt.args:
            _plain(arg, {"expr", "print_form"})
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS | _OWNED_PRINT_FORMS,
                     "print argument needs a scalar leaf")
            plan = self.value(arg.expr)
            arguments.append(f.splice(plan))
            # A view prints the leaf it views.
            typ = through_view(plan.shape_of().type)
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS and primitive_leaf(typ)
                     or arg.print_form in _OWNED_PRINT_FORMS and primitive_owned_leaf(typ),
                     "print argument needs a scalar leaf")
        # The arguments of the variadic print call evaluate in an unspecified order.
        self.argument_order_rule(stmt, tuple(arg.expr for arg in stmt.args))
        f.append(MIRPrint(tuple(arguments), stmt.loc))
        return f

    def range_loop(self, stmt: th.THIRForRange, loops: int) -> Fragment:
        _plain(stmt, {"var", "elem_type", "start", "stop", "start_is_literal", "stop_is_literal",
                      "body", "step_kind", "orelse", "hoist_loop_var", "target_written",
                      "hoist_decls", "hoisted_bindings"})
        _require(stmt, stmt.elem_type == INT32 and stmt.step_kind in ("plus_one", "unit_neg"),
                 "range needs int32 unit step")
        _require(stmt, isinstance(stmt.var, str) and bool(stmt.var) and isinstance(stmt.stop, th.THIRExpr),
                 "missing range target or stop")
        _require(stmt, type(stmt.hoist_loop_var) is bool and type(stmt.target_written) is bool,
                 "invalid range binding facts")
        hoists = self.hoists(stmt)
        bounds: list[Fragment | None] = []
        for bound, literal in ((stmt.start, stmt.start_is_literal), (stmt.stop, stmt.stop_is_literal)):
            _require(stmt, type(literal) is bool and literal is (bound is None or _literal(bound)),
                     "range bound capture disagrees with expression")
            plan = None
            if bound is not None:
                admitted = _literal(bound) or isinstance(bound, (th.THIRName, th.THIRFieldAccess, th.THIRSubscript))
                plan = self.expr(bound, INT32) if admitted else None
                _require(bound, plan is not None and plan.shape_of().type == INT32, "unsupported range bound")
            bounds.append(plan)
        _require(stmt, stmt.var not in self.range_counters, "nested range target changes outer induction")
        _require(stmt, self.bound(stmt.var) is not None and self.bound(stmt.var).type == INT32
                 if stmt.hoist_loop_var else stmt.var not in self.scope,
                 "range target residence mismatch")
        counters, scope = self.range_counters.copy(), self.scope.copy()
        slot = (self.slot_of(stmt.var) if stmt.hoist_loop_var
                else self.bind(stmt.var, scalar_shape(INT32), loc=stmt.loc).slot)
        # An unwritten, unhoisted target is the counter itself; any other is
        # a copy of it per iteration.
        direct = not stmt.hoist_loop_var and not stmt.target_written
        if direct:
            self.range_counters.add(stmt.var)
        f = self.fragment()
        entry, normal, after = f.block(), f.block(), f.block()
        head, then, advance = f.block(), f.block(), f.block()
        with self.temp_in(self.temp_scope(stmt, "counter")), self.temp_in(self.temp_scope(stmt, "loop")):
            self.loops.append((advance.id, after.id))
            body = self.scoped(stmt.body, loops + 1)
            self.loops.pop()
        self.range_counters, self.scope = counters, scope
        with self.temp_in(self.temp_scope(stmt, "else") if stmt.orelse else None):
            orelse = self.scoped(stmt.orelse, loops)
        loc = stmt.loc
        step = 1 if stmt.step_kind == "plus_one" else -1
        self.place_hoists(f, hoists, loc)
        start = f.splice(bounds[0]) if bounds[0] is not None else f.leaf(INT32, MIRConstant(0), loc)
        stop = f.splice(bounds[1])
        f.end(MIRGoto(entry.id, loc))
        f.current = entry
        with f.scope():
            # The counter's own blocks are its scope's.
            head.region = then.region = advance.region = f.region
            counter = f.adopt(slot) if direct else f.slot(scalar_shape(INT32), MIRSlotKind.LOCAL)
            f.write(counter, MIRRead(MIRPlace(start)), loc)
            f.end(MIRGoto(head.id, loc))
            f.current = head
            cond = f.leaf(BOOL, MIRCompare("<" if step == 1 else ">", counter, stop), loc)
            f.branch(cond, then.id, normal.id, loc)
            f.current = then
            with f.scope():
                if not direct:
                    if not stmt.hoist_loop_var:
                        f.adopt(slot)
                    f.write(slot, MIRRead(MIRPlace(counter)), loc)
                self.sequence(f, body)
            if f.current is not None:
                f.end(MIRGoto(advance.id, loc, _last_loc(stmt.body)))
            f.current = advance
            f.write(counter, MIRRangeAdvance(counter, step), loc)
            f.end(MIRGoto(head.id, loc))
        f.current = normal
        self.scoped_into(f, orelse)
        if f.current is not None:
            f.end(MIRGoto(after.id, loc, _last_loc(stmt.orelse)))
        f.current = after
        return f

    def native_loop(self, stmt: th.THIRForEach, loops: int) -> Fragment:
        _plain(stmt, {"var", "elem_type", "iterable", "body", "const_loop_var", "iterable_lvalue",
                      "orelse", "hoist_loop_var", "hoist_decls", "hoisted_bindings", "iteration"})
        # A container view a stub returns, or a container a user call returns
        # by reference, is a call result the loop holds.
        view = isinstance(stmt.iterable, (th.THIRCall, th.THIRMethodCall))
        _require(stmt, (stmt.iterable_lvalue is True or view)
                 and type(stmt.const_loop_var) is bool and type(stmt.hoist_loop_var) is bool
                 and isinstance(stmt.var, str) and bool(stmt.var), "missing or invalid native iteration facts")
        # Any container place: a borrowed, owned or viewed binding, a field, or a view a stub returns.
        place = self.container_place(stmt.iterable)
        shape = place.shape_of()
        # A cursor binds the source's declared element, one at a time.
        _require(stmt, binds_cursor(shape.type), "missing or invalid native iteration facts")
        element = shape.element
        # An aggregate element is bound over its container's region, at the
        # access the element has there; a write through a loop variable
        # bound readonly is a store through readonly storage, which the
        # validator refuses.
        reference = (value_shape(element.type, access=loan_access(
            element.type, element.cls, Local(const=stmt.const_loop_var, source_readonly=element.readonly)),
                                 role=MIRRole.LOAN)
                     if element.cls in AGGREGATE_CLASSES else None)
        # An owned-leaf element is read through a view of it, or a readonly borrow.
        view = element.cls is MIRClass.OWNED_LEAF and view_leaf(stmt.elem_type)
        typ = stmt.elem_type if view else element.type
        _require(stmt, (view_compatible(stmt.elem_type, element.type) if view
                        else unwrap_readonly(stmt.elem_type) == typ)
                 and stmt.binding.loop_binding in (LoopBinding.VALUE, LoopBinding.ASSIGN, LoopBinding.REFERENCE,
                                                   LoopBinding.CONST_REFERENCE)
                 and (reference is None or not stmt.hoist_loop_var)
                 and (element.cls is not MIRClass.OWNED_LEAF or not stmt.hoist_loop_var and (
                     view or stmt.binding.loop_binding is LoopBinding.CONST_REFERENCE)),
                 "unsupported native target binding")
        # The iterator walks the slot holding the place: a binding's own, or a holder of a field or a call result.
        holder = place.holder_shape_of()
        iterator = value_shape(holder.type, access=holder.readonly, role=MIRRole.CURSOR)
        hoists = self.hoists(stmt)
        _require(stmt, stmt.var not in self.range_counters and stmt.var not in self.iteration_references,
                 "nested loop replaces outer iteration binding")
        _require(stmt, self.bound(stmt.var) is not None and self.bound(stmt.var).type == typ
                 if stmt.hoist_loop_var else stmt.var not in self.scope,
                 "native target residence mismatch")
        targets, scope = self.iteration_references.copy(), self.scope.copy()
        if reference is not None or view or element.cls is MIRClass.OWNED_LEAF:
            self.iteration_references.add(stmt.var)
        if stmt.hoist_loop_var:
            slot = self.slot_of(stmt.var)
        else:
            slot = self.bind(stmt.var, reference if reference is not None
                             else view_holder_shape(stmt.elem_type) if view
                             else owned_leaf_shape(element.type, MIRRole.LOAN)
                             if element.cls is MIRClass.OWNED_LEAF else scalar_shape(stmt.elem_type), loc=stmt.loc).slot
        f = self.fragment()
        head, then, advance, normal, after = (f.block() for _ in range(5))
        with self.temp_in(self.temp_scope(stmt, "loop")):
            self.loops.append((advance.id, after.id))
            body = self.scoped(stmt.body, loops + 1)
            self.loops.pop()
        self.iteration_references = targets
        self.scope = scope
        with self.temp_in(self.temp_scope(stmt, "else") if stmt.orelse else None):
            orelse = self.scoped(stmt.orelse, loops)
        loc = stmt.loc
        self.place_hoists(f, hoists, loc)
        holder = f.splice(self.holding(place, raises=False))
        cursor = f.slot(iterator)
        f.write(cursor, MIRIteratorInit(holder), loc)
        f.end(MIRGoto(head.id, loc))
        f.current = head
        f.branch(f.leaf(BOOL, MIRIteratorHasNext(cursor), loc), then.id, normal.id, loc)
        f.current = then
        with f.scope():
            if not stmt.hoist_loop_var:
                f.adopt(slot)
            f.write(slot, MIRIteratorRead(cursor), loc)
            self.sequence(f, body)
        if f.current is not None:
            f.end(MIRGoto(advance.id, loc, _last_loc(stmt.body)))
        f.current = advance
        f.write(cursor, MIRIteratorAdvance(cursor), loc)
        f.end(MIRGoto(head.id, loc))
        f.current = normal
        self.scoped_into(f, orelse)
        if f.current is not None:
            f.end(MIRGoto(after.id, loc, _last_loc(stmt.orelse)))
        f.current = after
        return f

    def hoists(self, stmt: th.THIRIf | th.THIRWhile | th.THIRForRange | th.THIRForEach
               ) -> tuple[tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None], ...]:
        facts = stmt.hoisted_bindings
        _require(stmt, all(isinstance(f, th.THIRHoistedBinding) for f in facts)
                 and tuple(f.name for f in facts) == tuple(decl.name for decl in stmt.hoist_decls),
                 "missing or inconsistent hoisted binding facts")
        hoisted: list[tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None]] = []
        for fact in facts:
            _require(stmt, fact.initially_assigned is False and fact.placement is th.THIRStoragePlacement.SCOPE,
                     "invalid hoisted binding placement or availability")
            hoisted.append(self.hoisted_binding(stmt, fact.name, fact.type, fact, fact.optional_record_storage,
                                                fact.borrowed_record, fact.physical_default))
        return tuple(hoisted)

    def hoisted_binding(self, node: object, name: str, typ: TpyType, layout: object,
                        optional_record_storage: th.THIRBorrowedRecord | None = None,
                        borrowed_record: th.THIRBorrowedRecord | None = None, default: object = None
                        ) -> tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None]:
        """One binding declared before the statement that first assigns it,
        unassigned until then (`place_hoists` places it): its slot of type
        `typ` as `layout`'s layout fact lays it out, the Optional record
        backing it engages, and the physical default a scalar wrapper starts
        at. A hoisted statement binding and a frame's local alike."""
        _require(node, name not in self.scope, "invalid hoisted binding placement or availability")
        laid = self.laid_out(node, typ, holder=layout, others=("optional_record_storage",))
        backing = None
        if optional_record_storage is not None:
            reference = optional_record_storage
            problem = lent_shape_problem(typ, reference)
            _require(node, problem is None, problem)
            # The body's own backing storage, which assignments engage, and
            # its holder: at the storage's own access, never readonly.
            _require(node, not reference.readonly, "optional record backing must be mutable")
            definition = self.definitions.get(node, reference.type)
            _require(node, definition.layout.movable, "optional backing needs movable record")
            self.records[reference.type] = definition
            backing = _owned_optional(reference.type)
            problem = shape_problem(backing, backing.type, producer=True)
            _require(node, problem is None, problem)
            shape = value_shape(reference.type, access=False, role=MIRRole.LOAN)
        elif borrowed_record is not None:
            # A hoisted aggregate holder: of its declared type, at the
            # access THIR decided for it, as any local's.
            cls = _aggregate_class(typ)
            _require(node, laid is None and cls is not None
                     and isinstance(borrowed_record, th.THIRBorrowedRecord)
                     and type(borrowed_record.readonly) is bool, "unsupported hoisted record")
            problem = admission_problem(native_container_subject(typ), cls)
            _require(node, problem is None, problem)
            shape = _aggregate_holder(typ, borrowed_record.readonly)
        elif laid is not None:
            shape = laid
            _require(node, shape.cls is not MIRClass.UNION or shape.scalar_wrapper,
                     "unsupported hoisted union storage")
        else:
            _require(node, storage_leaf(typ), "unsupported hoisted value")
            shape = scalar_shape(typ)
        if shape.scalar_wrapper:
            # A union defaults to its first alternative's zero, an Optional to absent.
            first = shape.alternatives[0] if shape.cls is MIRClass.UNION else None
            expected = None if first is None else zero_value_of(first.type)
            _require(node, (first is None or expected is not None)
                     and isinstance(default, th.THIRWrapperDefault)
                     and type(default.alternative) is int and default.alternative == 0
                     and type(default.value) is type(expected) and default.value == expected,
                     "missing or inconsistent physical wrapper default")
        else:
            _require(node, default is None, "physical default on non-wrapper hoist")
        # An Optional record's backing, empty until a branch assigns it.
        bound = self.bind(name, shape, backing, loc=node.loc, backing_kind=MIRSlotKind.LOCAL)
        return bound.slot, bound.backing, (default.alternative, default.value) if default is not None else None

    @contextmanager
    def nested(self) -> Iterator[None]:
        """A nested statement list: the names it binds go out of scope after
        it, and an owned local stays fixed only if it stays fixed there."""
        scope, tuple_roots, fixed_owned = self.scope.copy(), self.tuple_roots.copy(), self.fixed_owned.copy()
        try:
            yield
        finally:
            self.scope, self.tuple_roots = scope, tuple_roots
            self.fixed_owned = fixed_owned & self.fixed_owned

    def scoped(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> list[Fragment]:
        with self.nested():
            return self.declarations(stmts, loops)


@dataclass(frozen=True, eq=False)
class MIRLoweredStorage:
    function: MIRFunction
    # Captured where the lowering places storage, never reconstructed from IDs.
    backings: Mapping[th.THIRExpr, MIRPlace]
    operations: Mapping[th.THIRStmt, tuple[MIRPoint, ...]]
    # The body's one preparation (`prepare.prepare`), which every later
    # pass reads: or the first dependency run's refusal.
    prepared: MIRPrepared | MIRNotCovered


def lower_function(fn: th.THIRFunction, body: MIRBodyId, *,
                   definitions: MIRDefinitions | None = None,
                   summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None) -> MIRFunction | MIRNotCovered:
    """The caller supplies trusted summaries from workspace analysis; the
    body kind is the function's own (`function_body_kind`).

    Structural validation checks their contracts, not another body's semantics.
    """
    result = lower_function_storage(fn, body, definitions=definitions, summaries=summaries)
    return result.function if isinstance(result, MIRLoweredStorage) else result


def _lower(lowering: _Lowering) -> MIRLoweredStorage | MIRNotCovered:
    """Check and build the body, then close it; a refusal of either is the body's verdict."""
    mir = lowering.mir
    try:
        lowering.check()
        if mir.refusal is not None:
            # A refusal of what the body reaches, after every refusal of the check.
            raise MIRUnsupported(*mir.refusal)
        try:
            function, first = assemble(mir, **lowering.assembly)
            prepared = validated(function, first)
        except MIRRepeatedInitializationError as failure:
            raise MIRUnsupported(failure, str(failure)) from failure
        except (MIRPresenceError, MIRDefiniteAssignmentError, MIRFallthroughError,
                MIRRegionStorageError) as failure:
            raise MIRUnsupported(lowering.node, str(failure)) from failure
        return MIRLoweredStorage(function, MappingProxyType(mir.backing_places),
                                 MappingProxyType(mir.borrow_operations), prepared)
    except MIRUnsupported as failure:
        return MIRNotCovered(mir.body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))


def lower_function_storage(fn: th.THIRFunction, body: MIRBodyId, *,
                           definitions: MIRDefinitions | None = None,
                           summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                           ) -> MIRLoweredStorage | MIRNotCovered:
    """The ordinary lowering pass, retaining its actual backing correspondence."""
    # Resumable, generic and closure bodies never reach this pass: the
    # collector and the workspace refuse them before scheduling a lowering.
    return _lower(_Lowering(fn, body, definitions if definitions is not None else MIRDefinitions(), summaries))


def lower_constructor(ctor: th.THIRConstructor, body: MIRBodyId, *,
                      definitions: MIRDefinitions | None = None,
                      summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None) -> MIRFunction | MIRNotCovered:
    """A constructor's body, building and returning its record (`constructor_body`)."""
    result = lower_constructor_storage(ctor, body, definitions=definitions, summaries=summaries)
    return result.function if isinstance(result, MIRLoweredStorage) else result


def lower_constructor_storage(ctor: th.THIRConstructor, body: MIRBodyId, *,
                              definitions: MIRDefinitions | None = None,
                              summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                              ) -> MIRLoweredStorage | MIRNotCovered:
    try:
        definitions = definitions if definitions is not None else MIRDefinitions()
        built = constructor_body(ctor, definitions.records)
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))
    # THIR's receiver fact for the record the constructor builds, which `self` reads and writes.
    fn = replace(constructor_function(ctor), receiver=th.THIRBorrowedRecord(built.layout.type, False))
    return _lower(_Lowering(fn, body, definitions, summaries, built))


def lower_frame_storage(leaves: th.THIRResumableLeaves, body: MIRBodyId, *,
                        definitions: MIRDefinitions | None = None,
                        summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                        ) -> MIRLoweredStorage | MIRNotCovered:
    """A generator's body, read off the frame and leaves its emission
    published (`_Lowering.frame_body`)."""
    return _lower(_Lowering(None, body, definitions if definitions is not None else MIRDefinitions(), summaries,
                            frame=leaves))


def lower_callable(source: th.THIRFunction | th.THIRConstructor | th.THIRResumableLeaves, body: MIRBodyId,
                   definitions: MIRDefinitions, summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult]
                   ) -> MIRLoweredStorage | MIRNotCovered:
    """A function's, a constructor's or a generator's body, lowered with its backing correspondence."""
    if isinstance(source, th.THIRConstructor):
        return lower_constructor_storage(source, body, definitions=definitions, summaries=summaries)
    if isinstance(source, th.THIRResumableLeaves):
        return lower_frame_storage(source, body, definitions=definitions, summaries=summaries)
    return lower_function_storage(source, body, definitions=definitions, summaries=summaries)
