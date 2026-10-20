"""All-or-nothing lowering of scalar and record-storage THIR operations."""

from contextlib import contextmanager
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import NamedTuple
from types import MappingProxyType

from ..identity_map import IdentityMap
from ..codegen_cpp.forms import LoopBinding
from ..type_def_registry import is_array
from ..parse import ResultForm, RebindStorage
from ..thir import nodes as th
from ..thir.source import Held
from ..thir.temp_plan import if_chain, validate_plan
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, container_view, converted_literal, declared_members, holds_loan, inline_member_type,
    leaf_constant, leaf_global, native_container_subject, native_container_type, optional_record_field,
    owned_constant, owned_leaf,
    owned_value_type, primitive_leaf, primitive_owned_leaf, record_type, storage_leaf, value_class,
    view_compatible, view_leaf,
)
from ..type_def_registry import ParamPassing, int_traits_of, type_def_of, zero_value_of
from ..typesys import (
    BOOL, INT32, NominalType, TpyType,
    NoneType, OptionalType, ReadonlyType, Representation, TupleType, VoidType,
    certified_primitive_comparison, holds_borrowing_view,
    return_representation, through_view, unwrap_own, unwrap_readonly, unwrap_ref_type, view_family_of, view_owned_leaf,
)
from .nodes import (
    MIRAlias, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBorrow, MIRCall, MIRCallStmt, MIRCompare, MIRConstant,
    MIRConstruct, MIRContainerElements, MIRDeref, MIRFunction, MIRGlobalId, MIRGoto, MIRIsAlternative,
    MIRIsPresent, MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRMemberInit,
    MIRMemberInitMode, MIRMemberInits, MIRMove, MIRNot, MIRNotCovered, MIROp, MIROptionalConstruct, MIROptionalCopy,
    MIROptionalPayload, MIRPayloadWriteMode, MIRPlace, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead, MIRReceiverInit,
    MIRRecordStorageInit, MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRStorageInit, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex, MIRTupleInitialization,
    MIRUnionConstruct, MIRUnionCopy, MIRUnionExtract, MIRUnionPayload, function_body_kind,
)
from .value import (
    Declared, Local, MIRField, MIRFieldId, MIRHolding, MIRPosition, MIRRecordLayout, MIRRole, MIRShape,
    container_members, fact_shape, holder_form, layout_fact, loan_access, member_compatible, member_shape,
    position_problem, shape_problem, thir_member, value_shape,
)
from .coverage import PAYLOAD_RECORD, MIRUnsupported, literal_type, plain as _plain, require as _require
from .definitions import (
    MIRComposedConstruct, MIRConstructorDefinition, MIRContainerDefinition, MIRDefinitions, MIRFieldInitializer,
    MIRHeldLayout, MIROwnedLeafDefinition, constructor_initialization, layout_copy_may_raise,
)
from .emit import (
    Fragment, MIRFallthroughError, MIRRegionStorageError, MIRValue, assemble, owned_leaf_shape, scalar_shape,
    view_holder_shape,
)
from .shapes import inline_payload_holder, step
from .call_contract import (
    BORROWING_PASSINGS, MIRCallSummary, MIRParameterBinding, MIRSummaryResult, MIRSummaryState, binds_at,
    call_result, call_path_problem, container_fact_problem, container_read_problem, container_result,
    native_container_problem, owned_record_result, bound_params, function_binding, region_view,
    stub_protocol_argument, stub_summary, summary_problem, view_result,
)
from .validate import MIRDefiniteAssignmentError, MIRPresenceError, MIRRepeatedInitializationError, reaches_element


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


def _optional_field_target(target: th.THIRExpr) -> bool:
    """An assignment target naming an Optional record field whole."""
    return (isinstance(target, th.THIRFieldAccess) and isinstance(target.field_identity, th.THIRFieldIdentity)
            and optional_record_field(target.field_identity.type) is not None)


def _call_arguments(expr: th.THIRCall | th.THIRMethodCall) -> tuple[th.THIRExpr, ...]:
    """The arguments a call binds in parameter order: a method's receiver is parameter 0."""
    return (expr.receiver, *expr.args) if isinstance(expr, th.THIRMethodCall) else expr.args


def _composes(initializers: tuple[MIRFieldInitializer, ...]) -> bool:
    return any(isinstance(init.source, MIRComposedConstruct) for init in initializers)


def _builds_container(initializers: tuple[MIRFieldInitializer, ...]) -> bool:
    """Whether a construct through these initializers builds a container
    member (its own or a composed member's)."""
    return any(native_container_type(init.field.type) or isinstance(init.source, MIRComposedConstruct)
               and _builds_container(init.source.initializers) for init in initializers)


def _declared_borrow(stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> th.THIRBorrowedRecord:
    """The record borrow a local declaration binds, at the access its
    declaration states (bound const or not)."""
    typ = stmt.resolved_type
    return th.THIRBorrowedRecord(unwrap_readonly(unwrap_ref_type(typ)),
                                 loan_access(typ, MIRClass.RECORD, Local(const=stmt.is_const)))


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
# holder's source, a container view's source, a container operand (an
# element place is refused there: its lend raises nothing, so it would drop
# a bounds exit), a record source.
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
_CONTAINER_PLACES = (_Place.NAME, _Place.FIELD)
_RECORD_PLACES = (_Place.NAME, _Place.SELF, _Place.FIELD)
# The places a field is read through: any record place but a global's; a
# binding holding a record is one by its holder, any other is checked.
_RECEIVER_PLACES = (_Place.NAME, _Place.SELF, _Place.FIELD, _Place.ELEMENT, _Place.UNION_PAYLOAD,
                    _Place.TUPLE_MEMBER)
_RECEIVER_REFUSALS = {_Place.FIELD: "field needs record storage", _Place.UNION_PAYLOAD: "field needs union record",
                      _Place.ELEMENT: "field needs record element",
                      _Place.TUPLE_MEMBER: "field needs tuple reference"}
# A binding of these classes is its own holder (`MIRValue.held`).
_HOLDER_BINDINGS = frozenset({MIRClass.RECORD, MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW, MIRClass.VIEW})


def _family_view(typ: TpyType) -> TpyType | None:
    """The view an owned leaf's family lends it through; None outside a view family."""
    family = view_family_of(typ)
    return family.view_type if family is not None else None


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

    def __init__(self, fn: th.THIRFunction, body: MIRBodyId, definitions: MIRDefinitions,
                 summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None,
                 initialization: MIRConstructorDefinition | None = None,
                 constructor: th.THIRConstructor | None = None) -> None:
        self.fn = fn
        self.initialization = initialization
        self.constructor = constructor
        self.parameters = {p.name for p in fn.params}
        # The body has a temporary plan in use, and the temporary plan scope
        # the statements being checked run in.
        self.planned = fn.temp_plan is not None and bool(fn.temp_plan.placements)
        self.temp_now = 0
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()
        self.definitions = definitions
        self.records: dict[NominalType, MIRConstructorDefinition | MIROwnedLeafDefinition | MIRHeldLayout] = {}
        # The body's root fragment: every slot and block is built into it.
        self.mir = Fragment(body=body, layouts=self.layouts, copy_may_raise=self.copy_may_raise)
        # The targets a `continue` and a `break` jump to, innermost loop last.
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []
        # The borrowed result the body returns: a record or a view.
        self.result: th.THIRBorrowedRecord | None = None
        # Locals holding their own record storage, and those never reseated
        # (a reseat may retarget the holder at another backing). The first
        # tells a reseated local (refused as such) from a parameter, which
        # is in neither.
        self.owned_records: set[str] = set()
        self.fixed_owned: set[str] = set()
        self.tuple_roots: dict[str, str] = {}
        self.body_declarations = {id(stmt) for stmt in fn.body if isinstance(stmt, th.THIRVarDecl)}
        self.globals: dict[MIRGlobalId, th.THIRGlobalBinding] = {}
        self.global_slots: dict[MIRGlobalId, MIRSlotId] = {}
        self.range_counters: set[str] = set()
        # A container result: owned storage moved out, or a borrow of a container place.
        self.container_result: th.THIRBorrowedRecord | None = None
        self.owned_container_result: NominalType | None = None
        # An `Own[R]` record result: the body's own record storage moved out.
        self.owned_record_result: NominalType | None = None
        self.iteration_references: set[str] = set()
        self.summaries = summaries if summaries is not None else {}
        self.calls: IdentityMap[th.THIRCall | th.THIRMethodCall, MIRCallSummary] = IdentityMap()
        # Each user call's borrowed result, at the access its receiver is bound at.
        self.call_results: IdentityMap[th.THIRCall | th.THIRMethodCall, th.THIRBorrowedRecord | None] = IdentityMap()
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
        if initialization is not None:
            # The receiver under construction has the body-side layout, whose
            # definition a caller may not construct through.
            self.records[initialization.layout.type] = initialization

    def layouts(self) -> Mapping[TpyType, MIRRecordLayout]:
        """The layouts of the records the body has registered so far."""
        return {t: d.layout for t, d in self.records.items()}

    def fragment(self) -> Fragment:
        return self.mir.fragment()

    def valued(self, value: MIRValue) -> Fragment:
        """A fragment of nothing but a value."""
        f = self.mir.fragment()
        f.value = value
        return f

    def bind(self, name: str, shape: MIRShape, backing: MIRShape | None = None, *, loc: object = None,
             backing_kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
             backing_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT,
             alias_source: MIRPlace | None = None) -> _Bound:
        """A local's slot (and the record storage it owns), placed where its
        declaration is built."""
        storage = (self.mir.record(backing, backing_kind, record_storage=backing_storage, loc=loc)
                   if backing is not None else None)
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

    def held(self, name: str | None) -> MIRShape | None:
        """The shape of a name's slot when it holds a record: the record and the holder's access."""
        shape = self.bound(name) if name is not None else None
        return shape if shape is not None and shape.is_pointer and shape.cls is MIRClass.RECORD else None

    def wraps(self, name: str, cls: MIRClass) -> bool:
        """A name bound as an Optional, a union or a tuple."""
        shape = self.bound(name)
        return shape is not None and shape.cls is cls

    def borrowed_container(self, name: str) -> bool:
        """A borrowed container binding: a container parameter, a container alias."""
        shape = self.bound(name)
        return shape is not None and shape.cls is MIRClass.CONTAINER and shape.holding is MIRHolding.REGION

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
        held = self.held(name)
        return held is not None and held == thir_member(fact)

    def view_bound(self, name: str) -> bool:
        """A view holder: a view parameter, local or loop target."""
        shape = self.bound(name)
        return shape is not None and shape.cls is MIRClass.VIEW

    def owned_container_bound(self, name: str) -> bool:
        """Owned container storage: a literal's local, an `Own[...]` parameter."""
        shape = self.bound(name)
        return shape is not None and shape.container_storage

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
        may), a tuple member. A binding of a reference type is its own
        holder (`held`); an owned leaf's storage is what a return moves out
        (`storage`). The reader admits it at its position first."""
        held = storage = None
        match place_kind(expr):
            case _Place.GLOBAL:
                _plain(expr, {"cpp", "global_binding"} if isinstance(expr, th.THIRModuleVar)
                       else {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
                root = self.global_place(expr)
            case _Place.NAME | _Place.SELF:
                name = "self" if isinstance(expr, th.THIRSelf) else expr.name
                slot, shape = self.slot_of(name), self.bound(name)
                root = MIRPlace(slot, (MIROptionalPayload(),) if shape.cls is MIRClass.OPTIONAL else ())
                held = slot if shape.cls in _HOLDER_BINDINGS else None
                storage = slot if shape.leaf_storage else None
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
                                    loc=expr.loc, held=held, storage=storage))

    def copy_may_raise(self, shape: MIRShape) -> bool:
        """Whether copying storage of `shape` may raise: an owned leaf's
        buffer as its type declares, a record by `layout_copy_may_raise`
        over the verified definitions, the record's own layout as the body has it."""
        if shape.cls is MIRClass.OWNED_LEAF:
            return bool(type_def_of(shape.type).copy_may_raise)

        def layout_of(t: NominalType) -> MIRRecordLayout | None:
            definition = self.definitions.records.get(t)
            if isinstance(definition, MIRConstructorDefinition):
                return definition.layout
            registered = self.records.get(t)
            return registered.layout if registered is not None else None
        return layout_copy_may_raise(layout_of(shape.type), layout_of)

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

    def call(self, expr: th.THIRCall | th.THIRMethodCall, *, temporary: bool = False) -> Fragment:
        """A call through its callee's summary, parameter by parameter over
        `_call_arguments`: a free-function call, or a resolved user method
        call rendered as the plain member call (a pointer receiver spells
        its access `->`). `temporary`: the result is materialized in
        storage of its full expression."""
        if isinstance(expr, th.THIRCall) and expr.stub_callee is not None:
            return self.stub_call(expr)
        storage = {"full_expression_storage"} if temporary else set()
        if isinstance(expr, th.THIRMethodCall):
            _plain(expr, {"receiver", "method_cpp", "args", "is_arrow", "resolved_callee", "receiver_access"}
                   | storage)
        else:
            _plain(expr, {"callee", "args", "callee_cpp", "resolved_callee"} | storage)
        callee = expr.resolved_callee
        _require(expr, isinstance(callee, th.THIRResolvedCallee), "call needs resolved ordinary callee")
        entry = self.summaries.get(callee.identity)
        _require(expr, entry is not None and entry.state is MIRSummaryState.KNOWN,
                 "call needs finalized known summary")
        summary = entry.summary
        # The signature the call publishes and the summary's bindings must be one fact.
        _require(expr, callee.signature.passings is not None, "signature passings unpublished")
        _require(expr, isinstance(summary, MIRCallSummary)
                 and callee.signature.passings == tuple(b.passing for b in summary.parameters),
                 "signature passings mismatch")
        _require(expr, summary_problem(summary) is None and summary.callee == callee,
                 "call summary signature or contract mismatch")
        result = call_result(callee, None)
        owned = owned_value_type(callee.signature.return_type)
        if result is not None and view_leaf(result.type):
            pass
        elif result is not None and native_container_type(result.type):
            # A container returned by reference: a place of the members its type derives.
            self.register_container(expr, result.type)
        elif result is not None:
            self.reference(expr, result, callee.signature.return_type)
            self.records[result.type] = self.definitions.get(expr, result.type)
        elif owned is not None:
            self.leaf_layout(expr, owned)
        arguments = _call_arguments(expr)
        # Every path the callee writes, returns or stores a loan under names
        # fields of the layouts the arguments bind, each registered here.
        problem = call_path_problem(
            summary, callee.signature.return_type,
            lambda parameter: self.bound_record(arguments[parameter]) if parameter < len(arguments) else None,
            lambda record: self.record_layout(expr, record))
        _require(expr, problem is None, problem)
        # An `Own[T]` return hands the caller a plain T.
        record = owned_record_result(callee.signature.return_type) if result is None else None
        _require(expr, (unwrap_readonly(unwrap_ref_type(expr.result_type)) == result.type if result is not None
                       else expr.result_type == owned if owned is not None
                       else expr.result_type == record if record is not None
                       else expr.result_type == callee.signature.return_type)
                 and len(_call_arguments(expr)) == len(summary.parameters), "call signature mismatch")
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
        return self.call_value(f, expr, summary, slots, receiver_readonly, expr.loc)

    def call_value(self, f: Fragment, node: th.THIRCall | th.THIRMethodCall | th.THIRSetItem,
                   summary: MIRCallSummary, arguments: list[MIRSlotId], receiver_readonly: bool,
                   loc: object) -> Fragment:
        """The call `node` makes over its argument slots (a method's receiver
        is parameter 0), its result's access decided once (`result_at`) and
        its shape stated (`result_shape`)."""
        self.calls[node] = summary
        result = self.call_results[node] = summary.result_at(receiver_readonly)
        absent = frozenset(i for i, sid in enumerate(arguments) if sid in self.mir.empty_arguments)
        call = MIRCall(summary, tuple(arguments), not summary.normal_return_only, loc,
                       isinstance(node, th.THIRMethodCall), absent)
        f.value = MIRValue(call, self.result_shape(node, summary, result), loc)
        return f

    def result_shape(self, node: th.THIRCall | th.THIRMethodCall | th.THIRSetItem, summary: MIRCallSummary,
                     result: th.THIRBorrowedRecord | None) -> MIRShape:
        """The shape of a call's result: an owned leaf fresh or lent
        (`owned_leaf_role`); a borrowed result at its access -- a view's
        holder, a container's or a container view's region, a record's
        pointer --; an `Own[R]` record's storage; else a scalar."""
        returns = summary.callee.signature.return_type
        if (owned := owned_value_type(returns)) is not None:
            return owned_leaf_shape(owned, owned_leaf_role(node, summary))
        if result is None:
            record = owned_record_result(returns)
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
        owned = owned_value_type(signature.return_type)
        if owned is not None:
            self.leaf_layout(expr, owned)
        _require(expr, (expr.result_type == owned if owned is not None
                        else expr.result_type == signature.return_type)
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
                    _require(arg, owned is None and view_result(signature.return_type) is None,
                             "stub result may borrow a container argument")
                    slots.append(f.lend(self.container_place(arg), raises=False))
                    self.writes[arg] = False
                    continue
                if owned_leaf(read):
                    slots.append(f.splice(self.operand(arg, self.lent_value(arg))))
                    continue
                # An owned-leaf or view result may borrow what the parameter
                # binds; a scalar argument has no storage to borrow.
                _require(arg, owned is None and view_result(signature.return_type) is None,
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
        owned = owned_value_type(signature.return_type)
        if owned is not None:
            self.leaf_layout(expr, owned)
        # A result borrowing the receiver (`setdefault`) needs a holder of its
        # region; a container view's is the view holder.
        result = summary.borrowed_result
        _require(expr, result is None if not view else result is not None and container_view(result.type),
                 "borrowed method result")
        # An `Own[T]` result hands the caller a plain T.
        _require(expr, (expr.result_type == owned if owned is not None
                        else expr.result_type == unwrap_own(signature.return_type))
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
        call = self.call_value(f, expr, summary, slots, shape.readonly, expr.loc)
        self.writes[expr] = False
        c = self.fragment()
        holder = c.lend(call)
        c.value = MIRValue(MIRPlace(holder), loc=expr.loc, held=holder)
        return c

    def argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """One argument at its binding, at a call and a constructor alike, as
        the slot the callee binds: an Optional holder, an owned leaf lent or
        copied, a view's loan, a record handed over or lent, a container,
        else any admitted scalar expression, evaluated into a temporary
        before the call (a scalar global is read there). Evaluation order
        among arguments is the caller's rule."""
        match binding.cls, binding.storage:
            case MIRClass.OPTIONAL, _:
                return self.optional_operand(arg, binding.value)
            case MIRClass.OWNED_LEAF, _:
                return self.owned_argument(arg, binding.type, binding.passing)
            case MIRClass.VIEW, _:
                # A view passed by value hands the callee the loan it holds.
                return self.holding(self.view_value(arg, binding.type))
            case MIRClass.RECORD, True:
                return self.handed_record_argument(binding, arg)
            case MIRClass.RECORD, False:
                return self.lent_record_argument(binding, arg)
            case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW, _:
                return self.container_argument(arg, binding)
        typ = arg.result_type if binding.protocol else binding.type
        plan = self.expr(arg, typ)
        _require(arg, plan.shape_of().type == typ, "call scalar argument mismatch")
        return plan

    def handed_record_argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """A record handed over by value (an `Own[R]` parameter, an element a
        container takes): built, copied, moved or handed over by a call into
        a temporary of the full expression the callee moves from, whose
        stored loans its construct, the copied or moved object or the
        callee's transfers name. A named record is refused."""
        if binding.private_owned:
            # The summary treats the object as private to the callee (what
            # it stores there is no transfer), which only a temporary no other
            # argument or holder of the caller's reaches makes true.
            _require(arg, isinstance(arg, th.THIRCtorCall) or _user_call(arg), "unsupported record argument")
        return self.record_operand(arg, binding.type)

    def lent_record_argument(self, binding: MIRParameterBinding, arg: th.THIRExpr) -> Fragment:
        """A record lent to a borrowed record parameter: the temporary THIR
        bound for it, a record source in place (a borrowed name, or a member
        read through one) at its own type or a struct-base ancestor's, never
        with more access than the binding lends, else a construct, copy or
        call result built into a temporary of the full expression and lent at
        the binding's access (never a move: binding the lent parameter moves
        nothing)."""
        ref = binding.lent_record
        if isinstance(arg, th.THIRArgTemp):
            return self.valued(MIRValue(self.argument_temporary(arg, ref).holder, loc=arg.loc))
        if isinstance(arg, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)):
            if not isinstance(arg, th.THIRFieldAccess):
                _require(arg, arg.form is th.Form.BORROW, "call needs borrowed record name")
            source = self.record_source(arg)
            self.writes[arg] = False
            f = self.holding(source)
            held = f.shape_of()
            _require(arg, self.binds_at(arg, held.type, ref.type) and (not held.readonly or ref.readonly),
                     "call record argument mismatch")
            self.lent_loans(arg, held.type, ref.type)
            return f
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRCopy)) or _user_call(arg),
                 "call needs borrowed record name")
        f = self.record_operand(arg, ref.type)
        g = self.fragment()
        g.value = MIRValue(g.lend(f, ref), loc=arg.loc)
        return g

    def container_argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> Fragment:
        """A container argument: a place lent whole for the call (a Span
        holder lends the region it views), or owned container storage moved
        into the temporary the callee takes over."""
        if binding.storage:
            _require(arg, isinstance(arg, th.THIRMove) and isinstance(arg.value, th.THIRName)
                     and arg.value.global_binding is None and self.owned_container_bound(arg.value.name),
                     "container argument needs an owned move")
            _plain(arg, {"value"})
            _plain(arg.value, {"name", "is_last_use", "is_movable"})
            _require(arg, self.bound(arg.value.name).type == binding.type, "call container argument mismatch")
            _require(arg, self.mir.active_temporaries is not None, "owned temporary needs full-expression boundary")
            # Owned container storage moved into the temporary the callee takes over.
            f = self.fragment()
            f.value = MIRValue(f.materialize(MIRValue(MIRMove(self.slot_of(arg.value.name)), binding.value, arg.loc),
                                             arg),
                               loc=arg.loc)
        else:
            _require(arg, self.container_receiver(arg), "call container argument needs a place")
            place = self.container_place(arg)
            shape = place.shape_of()
            _require(arg, shape.type == binding.type and (binding.readonly or not shape.readonly),
                     "call container argument mismatch")
            # A container place: lent whole, through a holder of it.
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

    def call_writes(self, expr: th.THIRCall | th.THIRMethodCall) -> bool:
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
        """A record built for a call into storage the temporary plan places,
        lent through a holder of it (`reference`, the record the parameter
        lends); `addr_of`: its address is what the argument passes (an
        Optional record parameter's pointer)."""
        _require(arg, self.fn.temp_plan is not None, "named argument needs complete temporary plan")
        placement = self.fn.temp_plan.placement(arg)
        _plain(arg, {"init", "cpp_type", "movable"} | ({"addr_of"} if addr_of else set()))
        _require(arg, arg.addr_of is addr_of, "named argument address mismatch")
        _require(arg, arg.form is th.Form.BORROW and (isinstance(arg.init, th.THIRCtorCall) or _user_call(arg.init))
                 and arg.result_type == reference.type and reference.readonly,
                 "named argument needs readonly record constructor")
        value = self.initializer(arg, lambda: self.record_value(arg.init, reference.type, call=True))
        self.stable_constructor_operands(arg.init)
        definition = self.records[reference.type]
        _require(arg, not placement.optional or definition.layout.movable and arg.would_bank(),
                 "deferred argument needs movable backing")
        temporary = self.placed(arg, placement, reference, value)
        self.argument_temporaries[arg] = temporary
        return temporary

    def placed(self, node: th.THIRArgTemp | th.THIRSlotEmplace, placement: object, holder: MIRShape,
               value: Fragment) -> _Temporary:
        """A record in storage the temporary plan places, lent through a
        holder of it (of shape `holder`); the statement declaring it places
        both slots."""
        storage = self.mir.record(value_shape(holder.type), MIRSlotKind.LOCAL, loc=node.loc,
                                  record_storage=(MIRRecordStorageKind.OPTIONAL if placement.optional
                                                  else MIRRecordStorageKind.DIRECT))
        holder = self.mir.record(holder, loc=node.loc)
        return _Temporary(node, node.loc, placement.index, placement.scope, placement.optional, storage, holder,
                          value)

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
        found = self.fn.temp_plan.initializations.get(node, ()) if self.fn.temp_plan is not None else ()
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
        _require(expr, self.fn.temp_plan is not None, "select storage needs complete temporary plan")
        placement = self.fn.temp_plan.placement(expr)
        _plain(expr, {"value", "cpp_type"})
        _require(expr, placement.optional and placement.initialization is expr,
                 "select storage needs conditional emplacement")
        _require(expr, isinstance(expr.value, th.THIRCtorCall), "select storage needs record constructor")
        value = self.initializer(expr, lambda: self.record_value(expr.value, holder.type))
        self.stable_constructor_operands(expr.value)
        _require(expr, self.records[holder.type].layout.movable, "select storage needs movable backing")
        temporary = self.placed(expr, placement, holder, value)
        self.select_temporaries[expr] = temporary
        return temporary

    def stable_constructor_operands(self, expr: th.THIRCtorCall | th.THIRCall | th.THIRMethodCall) -> None:
        # The temporary is initialized where the plan places it, which may
        # precede other operands: a call building it must write nothing.
        # Narrowed wrapper reads still require a selection proof.
        operands = expr.args if isinstance(expr, th.THIRCtorCall) else _call_arguments(expr)
        _require(expr, isinstance(expr, th.THIRCtorCall) or not self.call_writes(expr),
                 "named temporary needs stable scalar operands")
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
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
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
        and for an `Own[R]` record the holder the body reaches its own
        storage through."""
        shape = binding.value
        self.register(p, shape)
        if shape.cls is MIRClass.RECORD and shape.is_storage:
            # A record handed over at OWN binds the caller's temporary
            # (`R&&`), which ends with the caller's full expression. It
            # is modeled as the body's own storage ending at body exit:
            # an earlier end, so never a missed conflict. Reached through
            # a holder as an owned record local is; never reseated. The
            # loans it stores are the caller's, seeded at entry.
            self.owned_records.add(p.name)
            self.fixed_owned.add(p.name)
            return shape, holder_form(shape)
        return shape, None

    def register(self, node: object, shape: MIRShape, *, allow_owned: bool = False) -> None:
        """What the body's layouts need for a value of `shape`, its refusals
        raised against `node`: a container's members (`register_container`),
        an owned leaf's opaque layout, a tuple's inline records
        (`tuple_members`, an owning tuple only where `allow_owned`), record
        storage's verified definition."""
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
        fn = self.fn
        if fn.temp_plan is not None:
            validate_plan(fn.body, fn.temp_plan)
            for placement in fn.temp_plan.placements:
                _require(placement.node, not (isinstance(placement.node, th.THIRSlotEmplace)
                         and isinstance(placement.declaration, th.THIRWhile)),
                         "repeated condition select emplacement")
        _require(fn, fn.error_return_cpp is None, "error-return body")
        _require(fn, not fn.layout.hoisted_locals, "hoisted declarations")
        # A follows-receiver result has the access of the body's own receiver.
        receiver_readonly = fn.receiver is not None and fn.receiver.readonly
        signature = fn.resolved_callee.signature if fn.resolved_callee is not None else None
        result = call_result(fn.resolved_callee, receiver_readonly, declared=fn.return_type)
        if signature is not None and signature.borrowed_result is not None:
            self.reference(fn, result, fn.return_type)
            self.records[result.type] = self.definitions.get(fn, result.type)
            self.result = result
        elif (owned := owned_value_type(fn.return_type)) is not None:
            _require(fn, return_representation(fn.return_type) is Representation.STORAGE,
                     "unsupported return type")
            self.leaf_layout(fn, owned)
        elif view_leaf(fn.return_type):
            self.result = result
            _require(fn, self.result is not None, "unsupported return type")
        elif region_view(fn.return_type):
            # A container view result views a region the caller reaches through the
            # arguments: a borrowed result, summarized by its origins.
            self.register_container(fn, unwrap_readonly(unwrap_ref_type(fn.return_type)))
            self.result = result
            _require(fn, self.result is not None, "unsupported return type")
        elif native_container_type(bare := unwrap_readonly(unwrap_ref_type(fn.return_type))):
            # A container returned by reference is borrowed like a record result.
            self.result = self.container_result = result
            _require(fn, self.result is not None, "unsupported return type")
            self.register_container(fn, bare)
        elif native_container_type(bare := unwrap_own(fn.return_type)):
            # An `Own[...]` container returns the body's storage by value.
            _require(fn, return_representation(fn.return_type) is Representation.STORAGE, "unsupported return type")
            self.register_container(fn, bare)
            self.owned_container_result = bare
        elif (bare := owned_record_result(fn.return_type)) is not None:
            # The caller destroys what it receives: the record's verified definition.
            _require(fn, self.record_layout(fn, bare).movable, "owned record result needs movable record")
            self.owned_record_result = bare
        else:
            _require(fn, not container_view(unwrap_readonly(unwrap_ref_type(fn.return_type))),
                     "view of container storage")
            _require(fn, not holds_borrowing_view(fn.return_type), "view return")
            _require(fn, storage_leaf(fn.return_type, return_representation(fn.return_type))
                     or isinstance(fn.return_type, VoidType), "unsupported return type")
        mir = self.mir
        receiver = None
        # A parameter binds at the type its callers bind (`bound_params`). A
        # body without a signature of its types has no summary (which refuses
        # it as a signature mismatch), so it binds at its own.
        bound = bound_params(fn) or th.effective_params(fn)
        if fn.receiver is not None:
            param, *bound = bound
            binding = function_binding(param)
            if isinstance(binding, str):
                raise MIRUnsupported(fn, binding)
            shape, _ = self.parameter_slot(param, binding)
            self.parameters.add("self")
            receiver = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, "self"))
            self.bound_parameter(receiver, binding)
            self.scope["self"] = _Bound(receiver)
        names: dict[str, MIRSlotId] = {}
        for p, param in zip(fn.params, bound, strict=True):
            _plain(p, {"name", "type", "passing", "borrowed_record", "optional_layout", "union_layout", "tuple_layout",
                        "native_container"})
            _require(fn, p.name not in self.scope, "duplicate binding")
            # A body's parameter carries the facts THIR published for it.
            binding = function_binding(param)
            if isinstance(binding, str):
                raise MIRUnsupported(p, binding)
            shape, holder_shape = self.parameter_slot(p, binding)
            if holder_shape is not None:
                # The caller materialized the record before entry; the body
                # owns it and reaches it through a holder borrowed at entry.
                storage = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, p.name),
                                    MIRStorageDuration.BODY)
                self.bound_parameter(storage, binding)
                holder = mir.slot(holder_shape, MIRSlotKind.LOCAL)
                mir.write(holder, MIRBorrow(MIRPlace(storage)), None)
                self.scope[p.name] = _Bound(holder, storage)
                names[p.name] = storage
                continue
            # Owned storage lives for the body; a scalar union's payload is the caller's.
            sid = mir.adopt(mir.record(shape, MIRSlotKind.PARAMETER, p.name),
                            MIRStorageDuration.BODY if shape.is_storage
                            else MIRStorageDuration.CALLER if shape.cls is MIRClass.UNION and shape.scalar_wrapper
                            else None)
            self.bound_parameter(sid, binding)
            self.scope[p.name] = _Bound(sid)
            names[p.name] = sid
        self.sequence(mir, self.declarations(fn.body, 0))
        if self.planned:
            _require(fn, len(self.argument_temporaries) + len(self.select_temporaries) == len(fn.temp_plan.placements),
                     "temporary plan contains unsupported record storage")
        # Each callee once, as the body's summary table holds them.
        callees = tuple({s.callee.identity: s for s in self.calls.values()}.values())
        for identity, fact in self.globals.items():
            mir.records[self.global_slots[identity]].writable = fact.writable
        if self.initialization is not None:
            # The receiver's entry initialization builds every container
            # member, a composed member's included.
            pending = list(self.initialization.initializers)
            while pending:
                init = pending.pop()
                if isinstance(init.source, MIRComposedConstruct):
                    pending.extend(init.source.initializers)
                elif native_container_type(init.field.type):
                    self.register_container(self.constructor, init.field.type)
        self.close_layouts()
        receiver_init = None
        if self.initialization is not None:
            receiver_init = MIRReceiverInit(receiver, tuple(
                self.member_init(init, names) for init in self.initialization.initializers))
        self.assembly = dict(
            void=isinstance(fn.return_type, VoidType), return_type=fn.return_type,
            kind=MIRBodyKind.CONSTRUCTOR if self.initialization is not None else function_body_kind(fn),
            callees=callees, result=self.result, layouts=MappingProxyType(self.layouts()),
            receiver_init=receiver_init, parameter_bindings=tuple(self.parameter_bindings))

    def member_init(self, init: MIRFieldInitializer, names: Mapping[str, MIRSlotId]) -> MIRMemberInit:
        """One member's entry initialization from its verified initializer:
        a parameter's slot (an `Own[R]` parameter's storage, not the holder
        the body reaches it through), a constant, a container literal built
        over parameters, or a record member built by its own constructor,
        initialized field by field like the receiver."""
        match init.source:
            case str():
                source = names[init.source]
            case tuple():
                source = MIRConstruct(tuple(names[name] for name in init.source), True)
            case MIRComposedConstruct():
                source = MIRMemberInits(tuple(self.member_init(member, names)
                                              for member in init.source.initializers))
            case _:
                source = init.source
        return MIRMemberInit(source, init.mode, init.may_raise, init.loc)

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
                f.append(MIRRecordStorageInit(MIRPlace(storage), loc))
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
            fact = f.owned_initialization(dest) if placement is not None else None
            with f.full_expression(value):
                f.write(dest, f.splice(value), loc, fact)
        return f

    def put_container_alias(self, loc: object, slot: MIRSlotId, source: Fragment, call: bool,
                            operation: object) -> Fragment:
        """A local holding a container place: a binding's (aliased), a
        field's (borrowed), or the container a call returns by reference."""
        f = self.fragment()
        dest = f.adopt(slot)
        held = source.value.held
        place = f.splice(source)
        if call:
            f.borrow_operation(operation)
            f.write(dest, MIRAlias(held), loc)
        else:
            f.write(dest, MIRAlias(held) if held is not None else MIRBorrow(place), loc)
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
                f.payload_write(self.mir.records[dest].shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
        return f

    def put_union_assign(self, loc: object, slot: MIRSlotId, value: Fragment, inline_payload: bool) -> Fragment:
        f = self.fragment()
        f.write(slot, f.splice(value), loc, f.payload_write(inline_payload, MIRPayloadWriteMode.ASSIGN))
        return f

    def record_backing(self, f: Fragment, backing: tuple[object, MIRShape, MIRShape], value: Fragment,
                       write: object, duration: object) -> MIROptionalConstruct:
        """An Optional's payload built into its own record storage, lent through a holder."""
        loc, storage_shape, holder_shape = backing
        storage = f.slot(storage_shape, storage_duration=duration)
        f.write(storage, f.splice(value), loc, write)
        reference = f.holder(holder_shape, MIRBorrow(MIRPlace(storage)), loc)
        return MIROptionalConstruct(MIRPlace(reference))

    def put_optional_declare(self, loc: object, slot: MIRSlotId, value: Fragment, placement: bool | None,
                             backing: tuple[object, MIRShape, MIRShape] | None, operation: object | None,
                             missing: object | None) -> Fragment:
        f = self.fragment()
        dest = f.adopt(slot, self.declared_storage(f, placement, missing))
        written = (self.record_backing(f, backing, value, f.initial_write(), f.scoped_duration())
                   if backing is not None else f.splice(value))
        if operation is not None:
            # The holder points into the field: a loan replacing the field ends.
            f.borrow_operation(operation)
        f.write(dest, written, loc,
                f.payload_write(self.mir.records[dest].shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
        return f

    def put_optional_assign(self, loc: object, slot: MIRSlotId, value: Fragment,
                            backing: tuple[object, MIRShape, MIRShape] | None, in_place: bool,
                            inline_payload: bool) -> Fragment:
        """An Optional local's value replaced: from its source, or a record
        built into new backing or into its payload in place."""
        f = self.fragment()
        if backing is not None:
            f.write(slot, self.record_backing(f, backing, value, MIRRecordWrite(MIRRecordWriteMode.OWN_SITE),
                                              MIRStorageDuration.BODY), loc)
        elif in_place:
            f.write(MIRPlace(slot, PAYLOAD_RECORD), f.splice(value), loc,
                    MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, slot))
        else:
            f.write(slot, f.splice(value), loc, f.payload_write(inline_payload, MIRPayloadWriteMode.ASSIGN))
        return f

    def put_owned_record_declare(self, loc: object, slot: MIRSlotId, storage: MIRSlotId, value: Fragment,
                                 placement: bool | None, own_site: bool, missing: object | None) -> Fragment:
        """A local owning its record storage, reached through its holder;
        storage of a hoisted backing is initialized at its own site."""
        f = self.fragment()
        f.adopt(storage, self.declared_storage(f, placement, missing))
        write = MIRRecordWrite(MIRRecordWriteMode.OWN_SITE) if own_site else f.initial_write()
        with f.full_expression(value):
            f.write(storage, f.splice(value), loc, write)
        f.write(f.adopt(slot), MIRBorrow(MIRPlace(storage)), loc)
        return f

    def put_optional_record_assign(self, loc: object, slot: MIRSlotId, storage: MIRSlotId,
                                   value: Fragment) -> Fragment:
        """A hoisted Optional record backing filled and its holder repointed."""
        f = self.fragment()
        f.write(storage, f.splice(value), loc, MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
        f.write(slot, MIRBorrow(MIRPlace(storage)), loc)
        return f

    def put_replacement(self, loc: object, slot: MIRSlotId, value: Fragment, storage: MIRShape | None) -> Fragment:
        """A record holder's referent replaced: built into new storage
        (`storage`) or into the referent in place."""
        f = self.fragment()
        with f.full_expression(value):
            written = f.splice(value)
            if storage is not None:
                backing = f.slot(storage, storage_duration=MIRStorageDuration.BODY)
                f.write(backing, written, loc, MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))
                f.write(slot, MIRBorrow(MIRPlace(backing)), loc)
            else:
                f.write(MIRPlace(slot, (MIRDeref(),)), written, loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, slot))
        return f

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

    def put_owned_tuple_declare(self, loc: object, slot: MIRSlotId, value: Fragment) -> Fragment:
        """A tuple local built in its own storage from its members."""
        f = self.fragment()
        dest = f.adopt(slot, f.scoped_duration())
        f.write(dest, f.splice(value), loc, MIRTupleInitialization())
        return f

    def put_assign(self, loc: object, target: Fragment, value: Fragment, in_place: bool) -> Fragment:
        """A place written from a value of its own form (`in_place`: the
        storage it holds is replaced in place); the place is evaluated first."""
        f = self.fragment()
        place = f.splice(target)
        fact = MIRRecordWrite(MIRRecordWriteMode.IN_PLACE) if in_place else None
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
        f.write(local, f.splice(value), loc, f.owned_initialization(local))
        return f

    def put_append(self, loc: object, target: Fragment, typ: TpyType, value: Fragment) -> Fragment:
        """`t += v` in place: it reads the old value and v, then replaces t."""
        f = self.fragment()
        place = f.splice(target)
        with f.full_expression(value):
            operand = f.splice(value)
            current = f.holder(owned_leaf_shape(typ, MIRRole.LOAN), MIRBorrow(place), loc)
            f.write(place, MIROp("+=", (current, operand), may_raise=True), loc,
                    MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
        return f

    def put_jump(self, loc: object, head: bool) -> Fragment:
        """`continue` (`head`) or `break`."""
        f = self.fragment()
        f.end(MIRGoto(self.loops[-1][0 if head else 1], loc))
        return f

    def declarations(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> list[Fragment]:
        """The fragments of a statement list: each statement's, after the
        temporaries it declares and initializes (an if or a while carries
        its own)."""
        plans: list[Fragment] = []
        for stmt in stmts:
            if isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl)):
                plan = self.declaration(stmt, loops)
            else:
                plan = self.stmt(stmt, loops)
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
        plan = self.fn.temp_plan
        if not self.planned or (stmt not in plan.declarations and stmt not in plan.initializations):
            return None
        f = self.fragment()
        for temporary in map(self.temporary_of, (p.node for p in plan.declarations.get(stmt, ()))):
            if temporary is None or temporary.scope != self.temp_now:
                continue
            f.adopt(temporary.storage, f.scoped_duration())
            f.publish(temporary.key, MIRPlace(temporary.storage))
            f.adopt(temporary.holder)
            if temporary.optional:
                f.append(MIRRecordStorageInit(MIRPlace(temporary.storage), temporary.loc))
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
        return self.fn.temp_plan.scope(owner, role) if self.fn.temp_plan is not None else None

    def placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> bool | None:
        """Where storage a declaration owns lives: the body (a hoisted
        record backing), its scope, or no placement THIR gave (None)."""
        if (isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                and stmt.storage_placement is th.THIRStoragePlacement.BODY):
            return True
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
        if stmt.owned_storage is not None and loops:
            _require(stmt, isinstance(stmt.init, th.THIRCtorCall)
                     or stmt.storage_placement is th.THIRStoragePlacement.SCOPE
                     or (isinstance(stmt, th.THIRPtrLocalDecl)
                         and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                         and stmt.storage_placement is th.THIRStoragePlacement.BODY),
                     "loop copy or move needs scoped or hoisted storage")
        if (isinstance(stmt, th.THIRVarDecl) and stmt.native_container is not None
                and stmt.form is not th.Form.BORROW):
            return self.owned_container_local(stmt)
        if isinstance(stmt, th.THIRVarDecl) and stmt.native_container is not None:
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const",
                          "cpp_local_representation", "native_container"})
            fact = stmt.native_container
            self.container(stmt, fact, stmt.resolved_type)
            # The alias holds a container place: a borrowed binding, a field, or
            # the container a user call returns by reference.
            _require(stmt, (isinstance(stmt.init, (th.THIRName, th.THIRFieldAccess)) or _user_call(stmt.init))
                     and self.container_receiver(stmt.init),
                     "container alias needs fixed matching borrowed source")
            place = self.container_place(stmt.init)
            shape = place.shape_of()
            readonly = loan_access(stmt.resolved_type, MIRClass.CONTAINER, Local(const=stmt.is_const))
            _require(stmt, stmt.form is th.Form.BORROW and fact.readonly == readonly
                     and shape.type == fact.type and (readonly or not shape.readonly)
                     and (not isinstance(stmt.init, th.THIRName) or self.borrowed_container(stmt.init.name))
                     and stmt.name not in self.fn.layout.reassigned_locals,
                     "container alias needs fixed matching borrowed source")
            bound = self.bind(stmt.name, value_shape(fact.type, access=readonly, role=MIRRole.LOAN), loc=stmt.loc)
            return self.put_container_alias(stmt.loc, bound.slot, place, _user_call(stmt.init), stmt)
        if isinstance(stmt, th.THIRVarDecl) and stmt.tuple_storage_alias is not None:
            return self.tuple_alias(stmt)
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
            value = self.union_source(stmt.init, stmt.union_layout, stmt.union_literal)
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
            value = self.tuple_expr(stmt.init, allow_owned=owning)
            self.tuple_compatible(stmt, value.shape_of().members, members)
            bound = self.bind(stmt.name, shape, loc=stmt.loc)
            if owning and id(stmt) in self.body_declarations:
                self.tuple_roots[stmt.name] = stmt.name
            if not owning:
                return self.declare(stmt.loc, bound, self.tuple_copy(value, stmt.name))
            return self.put_owned_tuple_declare(stmt.loc, bound.slot, value)
        if stmt.owned_storage is not None:
            return self.owned(stmt)
        if stmt.alias_binding is not None or stmt.storage_borrow is not None:
            return self.borrow_binding(stmt, declaration=True)
        if ((_user_call(stmt.init) or isinstance(stmt.init, th.THIRIfExpr))
                and (isinstance(stmt, th.THIRPtrLocalDecl) or stmt.form is th.Form.BORROW)):
            return self.borrowed_binding(stmt, declaration=True)
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.BORROW
                and isinstance(stmt.init, th.THIRSubscript) and stmt.init.tuple_index is None
                and self.container_receiver(stmt.init.receiver)):
            return self.element_binding(stmt)
        _require(stmt, isinstance(stmt, th.THIRVarDecl), "missing alias binding")
        # THIR publishes no storage borrow for a narrowed Optional
        # field's payload (`storage.storage_borrow` keys on the field's
        # own type), so the binding has no fact to lower from.
        _require(stmt, not (isinstance(stmt.init, th.THIRFieldAccess) and stmt.init.narrowed_deref),
                 "optional payload borrow without a storage borrow fact")
        # A VALUE-form leaf is held by value whichever way its C++
        # declaration is spelled (an enum local names its type).
        _plain(stmt, {"name", "resolved_type", "init", "is_const", "cpp_type"})
        if owned_leaf(stmt.resolved_type):
            return self.owned_local(stmt)
        if view_leaf(stmt.resolved_type):
            return self.view_local(stmt)
        if region_view(stmt.resolved_type):
            return self.span_local(stmt)
        _require(stmt, not container_view(unwrap_readonly(unwrap_ref_type(stmt.resolved_type))),
                 "view of container storage")
        if native_container_type(native_container_subject(stmt.resolved_type)):
            raise MIRUnsupported(stmt, native_container_problem(None, stmt.resolved_type))
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
                fact = stmt.owned_storage
                _require(stmt, isinstance(fact, th.THIRBorrowedRecord)
                         and fact == stmt.optional_layout.payload
                         and stmt.is_const == fact.readonly
                         and isinstance(stmt.init, th.THIRCtorCall), "optional backing storage")
                value = self.record_value(stmt.init, fact.type)
                backing = (stmt.init.loc, value_shape(fact.type), _loan(fact))
            else:
                _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE and stmt.init is None,
                         "optional backing storage")
        _plain(stmt, allowed)
        if stmt.owned_storage is None:
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

    def owned_container_local(self, stmt: th.THIRVarDecl) -> Fragment:
        """A local holding its own container: built from a literal, the
        body's storage from its declaration."""
        _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation",
                      "native_container"})
        fact = stmt.native_container
        _require(stmt, stmt.form in (th.Form.VALUE, th.Form.STORAGE) and not fact.readonly and not stmt.is_const,
                 "unsupported local type or form")
        _require(stmt, stmt.name not in self.scope, "duplicate binding")
        # A declaration without a value would default-construct storage no write event names.
        _require(stmt, stmt.init is not None, "container declaration needs an initializer")
        self.container(stmt, fact, stmt.resolved_type)
        value = self.full_expression(stmt.init, check=lambda: self.container_value(stmt.init, fact.type))
        return self.declare(stmt.loc, self.bind(stmt.name, value_shape(fact.type), loc=stmt.loc), value)

    def container_value(self, expr: th.THIRExpr, typ: NominalType) -> Fragment:
        """Check what owned container storage of type `typ` takes: a literal
        with one operand per element, each in its member's form -- an inert
        leaf by value, an owned leaf lent for its slot's copy (or built), a
        record constructed into a temporary the literal moves from or copied
        through a holder."""
        # Any other source (a comprehension, a call, a copy) is not modeled yet.
        _require(expr, isinstance(expr, th.THIRContainerLiteral), "unsupported expression")
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
            return self.record_temporary(expr, typ, self.record_value(expr, typ, call=True))
        _require(expr, isinstance(expr, th.THIRCopy), "unsupported container element")
        # A copied element is lent to the literal through a holder of its source.
        return self.holding(self.record_value(expr, typ, copied_source=True))

    def record_temporary(self, expr: th.THIRExpr, typ: NominalType, value: Fragment) -> Fragment:
        """A record built, handed over by a call, copied or moved into storage
        of the full expression for an operation that moves it out (a
        container element, an owning parameter) or lends it. THIR publishes
        no backing for it: it is MIR's own temporary."""
        f = self.fragment()
        built = value.value
        f.splice(value)
        f.value = MIRValue(f.materialize(built, expr), loc=expr.loc)
        return f

    def element_binding(self, stmt: th.THIRVarDecl) -> Fragment:
        """A local binding one record element in place (`p = ps[0]`): a
        borrow of the container's element region, never retargeted."""
        _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation"})
        _require(stmt, stmt.name not in self.fn.layout.reassigned_locals, "element binding is reassigned")
        _require(stmt, stmt.name not in self.scope, "duplicate binding")
        fact = _declared_borrow(stmt)

        def check() -> Fragment:
            element = self.element_access(stmt.init)
            member = element.shape_of()
            _require(stmt, member.cls is MIRClass.RECORD and member.type == fact.type
                     and stmt.init.form is th.Form.BORROW and (not member.readonly or fact.readonly),
                     "element binding type or access mismatch")
            v = element.value
            element.value = MIRValue(MIRBorrow(v.value, v.raises), _loan(fact), v.loc)
            return element
        value = self.full_expression(stmt.init, check=check)
        return self.declare(stmt.loc, self.bind(stmt.name, _loan(fact), loc=stmt.loc), value)

    def span_local(self, stmt: th.THIRVarDecl) -> Fragment:
        """A container view local: a holder of the region its source views, readonly
        when its element is or when that region is."""
        typ = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
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
                f = self.span_value(expr.expr, unwrap_readonly(unwrap_ref_type(expr.expr.result_type)), target)
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
        _require(stmt, stmt.name not in self.fn.layout.reassigned_locals
                 and fact.source not in self.fn.layout.reassigned_locals
                 and self.tuple_roots[fact.source] not in self.fn.layout.reassigned_locals,
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
            return unwrap_readonly(unwrap_ref_type(arg.result_type))
        if isinstance(arg, th.THIRFieldAccess):
            # An inline member, or a narrowed Optional field's payload, is
            # storage of exactly its declared type.
            fact = arg.field_identity
            bare = unwrap_readonly(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
            if bare is not None and arg.narrowed_deref:
                bare = optional_record_field(bare)
            return bare if bare is not None and record_type(bare) else None
        name = arg.name if isinstance(arg, th.THIRName) else "self" if isinstance(arg, th.THIRSelf) else None
        held = self.held(name)
        return held.type if held is not None else None

    def close_layouts(self) -> None:
        """Every owned-leaf field of a record the body models is a place of
        that leaf's opaque storage, and every record field a place of its
        member record's storage, so their layouts come along, transitively."""
        pending = list(self.records.values())
        while pending:
            for member in pending.pop().layout.fields:
                if owned_leaf(member.type):
                    self.leaf_layout(self.fn, member.type)
                    continue
                bare = inline_member_type(member.type)
                if record_type(bare) and bare not in self.records:
                    self.records[bare] = self.definitions.get(self.fn, bare)
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
                raise MIRUnsupported(expr, "owned-leaf move")
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                # The callee returns an owned leaf by value: fresh storage for the
                # caller, unless a stub's result may borrow what the call lends --
                # a borrow of those arguments, through the holder it is lent by.
                f = self.call(expr)
                summary = self.calls[expr]
                result = summary.borrowed_result
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
                         and (result is None or result.type == typ and result.readonly),
                         "call owned result mismatch")
                writing = self.call_writes(expr)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                f = self.method_call(expr)
                summary = self.calls[expr]
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
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

    def record_value(self, expr: th.THIRExpr, typ: NominalType, *, temporary: bool = False,
                     call: bool = False, copied_source: bool = False) -> Fragment:
        """Check what record storage of type `typ` takes: a construct, a copy
        of a record source (`record_source`: spelled `copy(x)`, or a borrowed
        name, a member read or a form conversion THIR leaves to its form), a
        move of a fixed owned local, or -- where `call` admits it -- a call
        handing over an owned record result. `copied_source`: a copy's
        source itself, which a container literal reads through a holder."""
        definition = self.definitions.get(expr, typ)
        self.records[typ] = definition
        _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == typ,
                 "record initializer type mismatch")
        writing = False
        f = self.fragment()
        source = None
        match expr:
            case th.THIRCtorCall():
                _plain(expr, {"type_cpp", "args"} | ({"full_expression_storage"} if temporary else set()))
                _require(expr, expr.form is th.Form.STORAGE, "constructor form")
                # A caller's construct has one operand per member; a container
                # member is built by the constructor body itself.
                _require(expr, not _builds_container(definition.initializers), "constructor container field")
                if _composes(definition.initializers):
                    # A member built by its own constructor is built into storage
                    # of the full expression, then moved in.
                    _require(expr, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
                    self.mir.active_temporaries.append(expr)
                params = definition.constructor.params
                _require(expr, len(expr.args) == len(params), "incomplete constructor arguments")
                # A lent record binds only at its own type (TODO.md "Constructor arguments take the call's rules").
                for arg, binding in zip(expr.args, definition.bindings):
                    if binding.cls is MIRClass.RECORD and not binding.storage:
                        bound = self.bound_record(arg)
                        _require(arg, bound is None or bound == binding.type, "constructor argument type")
                arguments: dict[str, MIRSlotId] = {}
                for arg, param, binding in zip(expr.args, params, definition.bindings):
                    operand = self.argument(binding, arg)
                    _require(arg, not self.writes[arg], "effectful constructor argument")
                    arguments[param.name] = f.splice(operand)
                f.value = self.construct(f, typ, definition.initializers, arguments, expr.loc)
            case th.THIRCopy():
                _plain(expr, {"value", "cpp_type"})
                source = self.record_source(expr.value)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable and expr.form is th.Form.STORAGE,
                         "record is not copyable or copy form")
            case th.THIRMove():
                _plain(expr, {"value"})
                name = self.reference_name(expr.value)
                _require(expr, self.held(name).type == typ, "record source type mismatch")
                _require(expr, definition.layout.movable and name in self.fixed_owned
                         and not self.held(name).readonly
                         and expr.form is expr.value.form, "move needs fixed movable owned local")
                f.value = MIRValue(MIRMove(self.scope[name].backing), value_shape(typ), expr.loc)
            case (th.THIRName() | th.THIRSelf()) if expr.form is th.Form.BORROW:
                # A borrowed name read into record storage: C++ copies what
                # it names (sema warned about the inline copy).
                source = self.record_source(expr)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
            case th.THIRFieldAccess() if expr.form is th.Form.STORAGE:
                # A member read into record storage: a copy of that member.
                source = self.record_source(expr)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
            case th.THIRFormConvert():
                # A local's storage converted into storage: a copy of it.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and isinstance(expr.value, (th.THIRName, th.THIRSelf))
                         and expr.value.form is th.Form.BORROW, "unsupported form conversion")
                source = self.record_source(expr.value)
                _require(expr, source.shape_of().type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
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

    def construct(self, f: Fragment, typ: NominalType, initializers: tuple[MIRFieldInitializer, ...],
                  arguments: Mapping[str, MIRSlotId], loc: object) -> MIRValue:
        """A construct of record storage of type `typ` through a verified
        definition: each member from a parameter's argument (copied out of
        a lent holder or moved out of the handed-over temporary), a member
        built by its own constructor over the same arguments into storage of
        the full expression and then moved in, a literal a view member
        stores, an inert constant, or an Optional record member's holder.
        Only the member copies may raise; a composed member's own construct
        carries its copies."""
        fields: list[MIRSlotId] = []
        payloads: list[TpyType] = []
        for init in initializers:
            payload = (unwrap_readonly(init.field.type).inner
                       if value_class(init.field.type) is MIRClass.OPTIONAL else None)
            if payload is not None:
                payloads.append(payload)
                if not isinstance(init.source, str):
                    fields.append(self.optional_member(f, init, payload, arguments, loc))
                    continue
            if isinstance(init.source, MIRComposedConstruct):
                fields.append(f.materialize(self.construct(f, unwrap_readonly(init.field.type),
                                                           init.source.initializers, arguments, loc)))
            elif isinstance(init.source, str):
                fields.append(arguments[init.source])
            elif init.mode is MIRMemberInitMode.BORROW:
                # A literal a view member stores: a holder of its static storage.
                view = init.field.type
                fields.append(f.holder(view_holder_shape(view), init.source, init.loc))
            else:
                fields.append(f.leaf(init.field.type, init.source, init.loc))
        # An Optional record member takes an Optional holder of what its
        # payload copies; the member copy may raise whatever its source.
        raises = any(self.copy_may_raise(value_shape(payload)) for payload in payloads)
        construct = MIRConstruct(tuple(fields), raises or any(
            init.may_raise for init in initializers if not isinstance(init.source, MIRComposedConstruct)))
        return MIRValue(construct, value_shape(typ), loc)

    def optional_member(self, f: Fragment, init: MIRFieldInitializer, payload: NominalType,
                        arguments: Mapping[str, MIRSlotId], loc: object) -> MIRSlotId:
        """The operand of an Optional record member a constructor leaves
        empty or engages with a payload its own constructor builds: an
        Optional holder, empty or borrowing that payload built into storage
        of the full expression."""
        # The holder is a local the member's copy only reads: bound const whatever the member's access.
        fact = th.THIRBorrowedRecord(payload, loan_access(payload, MIRClass.RECORD, Local(const=True)))
        dest = f.slot(value_shape(init.field.type, facts=th.THIROptionalLayout(fact)))
        if isinstance(init.source, MIRComposedConstruct):
            storage = f.materialize(self.construct(f, payload, init.source.initializers, arguments, loc))
            holder = f.holder(_loan(fact), MIRBorrow(MIRPlace(storage)), loc)
            f.write(dest, MIROptionalConstruct(MIRPlace(holder)), loc)
        else:
            f.write(dest, MIROptionalConstruct(), loc)
        return dest

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
            # The holder the payload is built into is a temporary of the full
            # expression, so a loop condition initializes it once per pass.
            _require(arg, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
            self.mir.active_temporaries.append(arg)
            # A scalar payload lives in the region the call runs in, like a scalar Optional local's.
            f = self.fragment()
            dest = f.slot(shape, storage_duration=f.scoped_duration())
            f.write(dest, f.splice(value), arg.loc,
                    f.payload_write(shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
            f.value = MIRValue(dest, loc=arg.loc)
            return f
        f = self.fragment()
        match arg:
            case th.THIRArgTemp():
                temporary = self.argument_temporary(arg, target, addr_of=True)
                value: object = MIROptionalConstruct(MIRPlace(temporary.holder))
            case th.THIROptionalPtrArg(value=None):
                _plain(arg, set())
                value = None
            case th.THIROptionalPtrArg(lift=True):
                _plain(arg, {"value", "lift"})
                found = self.field(arg.value) if isinstance(arg.value, th.THIRFieldAccess) else None
                _require(arg, found is not None and found[0].cls is MIRClass.OPTIONAL
                         and found[0].payload.cls is MIRClass.RECORD, "unsupported optional argument")
                self.payload_compatible(arg, inline_payload_holder(found[0], self.layouts()), target)
                value = MIROptionalCopy(f.splice(found[1]))
            case th.THIROptionalPtrArg():
                _plain(arg, {"value", "addr_of"})
                _require(arg, isinstance(arg.value, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)),
                         "unsupported optional argument")
                source = self.record_source(arg.value)
                self.payload_compatible(arg, source.holder_shape_of(), target)
                value = MIROptionalConstruct(MIRPlace(f.lend(source)))
            case th.THIRName() if self.wraps(arg.name, MIRClass.OPTIONAL):
                # A narrowed name still passes its holder's pointer.
                extract = arg.optional_read is not None and arg.optional_read.extract
                self.payload_compatible(arg, thir_member(self.optional_name(arg, extract=extract).payload), target)
                value = MIROptionalCopy(MIRPlace(self.slot_of(arg.name)))
            case _:
                raise MIRUnsupported(arg, "unsupported optional argument")
        _require(arg, arg.form is th.Form.BORROW, "unsupported optional argument")
        self.writes[arg] = False
        dest = f.slot(shape)
        if value is None:
            f.write(dest, MIROptionalConstruct(), arg.loc)
            self.mir.empty_arguments.add(dest)
        else:
            f.write(dest, value, arg.loc)
        f.value = MIRValue(dest, loc=arg.loc)
        return f

    def record_operand(self, arg: th.THIRExpr, typ: NominalType) -> Fragment:
        """A record argument built into storage of its full expression: a
        construct, a call handing over its owned result, a copy, or a fixed
        owned local moved out."""
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRCopy, th.THIRMove)) or _user_call(arg),
                 "constructor argument type")
        # The temporary stores the loans its construct, the callee's
        # transfers or the copied or moved object name.
        _require(arg, self.mir.active_temporaries is not None, "temporary needs full-expression boundary")
        return self.record_temporary(arg, typ, self.record_value(arg, typ, call=True))

    def owned(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> Fragment:
        fact = stmt.owned_storage
        self.reference(stmt, fact, stmt.resolved_type)
        allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "owned_storage", "storage_placement"}
        if isinstance(stmt, th.THIRVarDecl):
            allowed.add("cpp_local_representation")
            _require(stmt, stmt.form in (th.Form.STORAGE, th.Form.BORROW), "owned declaration form")
        else:
            allowed.add("kind")
            _require(stmt, stmt.kind in (th.PtrSlotKind.RECORD_RVALUE, th.PtrSlotKind.RECORD_HOISTED),
                     "owned declaration kind")
            if stmt.kind is th.PtrSlotKind.RECORD_HOISTED:
                _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.BODY,
                         "hoisted backing needs body storage site")
        _plain(stmt, allowed)
        _require(stmt, stmt.init is not None and stmt.is_const == fact.readonly,
                 "owned declaration initializer or access")

        def initializer() -> Fragment:
            return self.record_value(stmt.init, fact.type, call=True)
        # A constructor argument may need a temporary (an owned leaf built for
        # it), which the declaration's full expression owns.
        value = self.full_expression(stmt.init, check=initializer)
        hoisted = isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
        if hoisted:
            _require(stmt, self.records[fact.type].layout.movable, "hoisted backing needs movable record")
        self.owned_records.add(stmt.name)
        bound = self.bind(stmt.name, _loan(fact), value_shape(fact.type), loc=stmt.loc)
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.STORAGE
                and stmt.name not in self.fn.layout.reassigned_locals):
            self.fixed_owned.add(stmt.name)
        # A hoisted backing's storage is the body's, initialized at its own site.
        return self.put_owned_record_declare(stmt.loc, bound.slot, bound.backing, value, self.placement(stmt), hoisted,
                                   self.missing_placement(stmt, True))

    def replacement(self, stmt: th.THIRAssign) -> Fragment:
        _plain(stmt, {"target", "value", "rebind_storage", "slot_cpp"})
        name = self.reference_name(stmt.target)
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                 "unsupported replacement storage")
        _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE
                 or not self.held(name).readonly, "readonly in-place replacement")
        typ = self.held(name).type

        def value() -> Fragment:
            return self.record_value(stmt.value, typ, call=True)
        # Its argument temporaries end with the replacing full expression.
        replacing = self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[self.held(name).type].layout.movable,
                 "replacement needs movable record")
        # A STORAGE-form local is one sema never rebinds (a rebound local is a
        # rebind-slot pointer local), so `fixed_owned` is a whole-body fact; a
        # reseat of a member would hand a returned backing back stale.
        _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")
        # OWN builds the new referent into storage of its own, IN_PLACE into the old one.
        return self.put_replacement(stmt.loc, self.slot_of(name), replacing, value_shape(typ)
                            if stmt.rebind_storage is RebindStorage.OWN else None)

    def member_write(self, stmt: th.THIRAssign) -> Fragment:
        """`ln.a = v`: an inline record member replaced whole, in place --
        the record around it keeps its identity, so the write is an event
        of the member place only. The value is what record storage takes
        (`record_value`); C++ copy- or move-assigns it."""
        _plain(stmt, {"target", "value"})
        member, place = self.field(stmt.target, write=True)
        _require(stmt, member.cls is MIRClass.RECORD and stmt.target.form is th.Form.STORAGE,
                 "record member write needs member storage")
        if holds_loan(member.type):
            # The write fills the member's entries under the object around it.
            self.held_root(stmt.target)

        def value() -> Fragment:
            return self.record_value(stmt.value, member.type, call=True)
        # Its argument temporaries end with the replacing full expression.
        replacing = self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[member.type].layout.movable, "record member replacement needs movable record")
        return self.put_assign(stmt.loc, place, replacing, True)

    def optional_field_write(self, stmt: th.THIRAssign) -> Fragment:
        """`h.p = v` on an Optional record field: the whole field replaced in
        place -- the record around it keeps its identity, so the write is an
        event of the field place, its payload included. The value empties
        it, copies another Optional's payload (a whole Optional field, or an
        Optional holder's payload), or is a record built, handed over or
        copied into storage of the statement and moved into the payload."""
        _plain(stmt, {"target", "value"})
        whole, target = self.field(stmt.target, write=True)
        _require(stmt, whole.cls is MIRClass.OPTIONAL, "optional field write needs the whole field")
        payload = whole.payload.type
        value = stmt.value

        def check() -> Fragment:
            f = self.fragment()
            if isinstance(value, th.THIRLiteral) and value.value is None:
                _plain(value, {"value", "none_cpp"})
                _require(value, isinstance(value.result_type, (OptionalType, NoneType, VoidType)),
                         "optional absence type")
                self.writes[value] = False
                f.value = MIRValue(MIROptionalConstruct(), whole, value.loc)
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
                temporary = self.record_operand(value, payload)
                _require(value, self.records[payload].layout.movable, "optional field payload needs movable record")
                f.value = MIRValue(MIROptionalConstruct(MIRPlace(f.splice(temporary))), whole, value.loc)
                return f
            _require(value, self.records[payload].layout.copyable, "record is not copyable or copy form")
            self.writes[value] = False
            source = f.splice(place)
            f.value = MIRValue(MIROptionalCopy(source, self.copy_may_raise(value_shape(payload))
                                               or reaches_element(source)),
                               whole, value.loc)
            return f
        return self.put_assign(stmt.loc, target, self.full_expression(value, check=check), True)

    def view_member_write(self, stmt: th.THIRAssign) -> Fragment:
        """`t.s = v`: a view member rebound to the loan `v` holds. The record
        keeps its identity and no storage is replaced; every object the
        place may reach stores the loan (`dependencies.store_loan`)."""
        _plain(stmt, {"target", "value"})
        shape, place = self.field(stmt.target, write=True)
        typ = shape.type
        # A loan held by a temporary of the statement ends with it, which
        # the scope-end analysis reports.
        return self.put_view_member_assign(stmt.loc, place, self.full_expression(
            stmt.value, check=lambda: self.holding(self.view_value(stmt.value, typ))))

    def reference(self, node: object, ref: th.THIRBorrowedRecord | MIRShape, typ: TpyType) -> None:
        """Check a borrowed-record fact, or the shape of a record a holder or
        a wrapper member reaches, against the type it is read at."""
        _require(node, isinstance(ref, th.THIRBorrowedRecord)
                 or isinstance(ref, MIRShape) and ref.cls is MIRClass.RECORD, "invalid reference fact")
        shape = fact_shape(typ, ref) if isinstance(ref, th.THIRBorrowedRecord) else shape_problem(ref, typ) or ref
        _require(node, not isinstance(shape, str), shape)

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
        self.held_root(arg)

    def held_root(self, place: th.THIRExpr) -> None:
        """The layout of the record a place's root name holds: a view member
        under it, through inline members, is keyed under that object, so
        the entry exists only with the root's layout (a borrowed
        parameter's included, which nothing else registers)."""
        root = place
        while isinstance(root, th.THIRFieldAccess):
            root = root.receiver
        name = root.name if isinstance(root, th.THIRName) else "self" if isinstance(root, th.THIRSelf) else None
        if (held := self.held(name)) is not None:
            self.held_layout(place, held.type)

    def held_layout(self, node: object, typ: NominalType) -> MIRRecordLayout:
        """The layout of a record the body reaches through a holder, which a
        caller need not be able to construct (`MIRDefinitions.held_layout`)."""
        if typ not in self.records:
            self.records[typ] = self.definitions.held_layout(node, typ)
        return self.records[typ].layout

    def register_container(self, node: object, typ: TpyType) -> tuple[MIRShape, ...]:
        """The mutable-access members of a native container (or of the region a
        Span views); its record members' definitions and owned-leaf members'
        layouts come along, since places of its elements reach them."""
        bare = unwrap_readonly(unwrap_ref_type(typ))
        _require(node, isinstance(bare, NominalType) and (native_container_type(bare) or container_view(bare)),
                 "unsupported native container type")
        definition = self.definitions.get(node, bare)
        _require(node, isinstance(definition, MIRContainerDefinition), "unsupported native container type")
        for record in definition.records:
            self.records[record.layout.type] = record
        for member in definition.members:
            if member.cls is MIRClass.OWNED_LEAF:
                self.leaf_layout(node, member.type)
        return definition.members

    def container(self, node: object, fact: th.THIRNativeContainer, typ: TpyType) -> None:
        """Check a local's container fact against its type and the members
        the type derives; the fact's element is the member iteration
        yields."""
        problem = container_fact_problem(fact, typ) or native_container_problem(fact, typ)
        _require(node, problem is None, problem)
        self.register_container(node, fact.type)

    def container_name(self, expr: th.THIRExpr) -> MIRShape:
        """A borrowed container binding read by name: its slot's shape."""
        _require(expr, isinstance(expr, th.THIRName), "container source needs local name")
        _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
        shape = self.bound(expr.name) if self.borrowed_container(expr.name) else None
        _require(expr, shape is not None and expr.form is th.Form.BORROW
                 and expr.name not in self.fn.layout.reassigned_locals, "container source needs fixed borrowed binding")
        problem = container_read_problem(shape.type, shape.readonly, expr.result_type)
        _require(expr, problem is None, problem)
        return shape

    def container_receiver(self, expr: th.THIRExpr) -> bool:
        """Whether `expr` names a container place: a borrowed, owned or
        viewed container binding, a container field, or a container view a
        method stub returns."""
        match expr:
            case th.THIRName() if expr.global_binding is None:
                return (self.borrowed_container(expr.name) or self.owned_container_bound(expr.name)
                        or self.container_view_access(expr.name) is not None)
            case th.THIRFieldAccess():
                fact = expr.field_identity
                return isinstance(fact, th.THIRFieldIdentity) and native_container_type(unwrap_readonly(fact.type))
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                # A user callee's container returned by reference.
                callee = expr.resolved_callee
                return (isinstance(callee, th.THIRResolvedCallee)
                        and container_result(callee.signature.return_type) is not None)
            case th.THIRMethodCall():
                # A container view a method stub returns views its receiver's region.
                return expr.stub_callee is not None and container_view(unwrap_readonly(unwrap_ref_type(expr.result_type)))
        return False

    def container_place(self, expr: th.THIRExpr) -> Fragment:
        """Check a container place expression, as its place (its members
        under the access the place has); `held` is a binding or call holder
        that is the place."""
        kind = self.admitted_place(expr, _CONTAINER_PLACES)
        match expr:
            case th.THIRName() if kind is _Place.NAME and self.borrowed_container(expr.name):
                self.container_name(expr)
            case th.THIRName() if kind is _Place.NAME and (self.owned_container_bound(expr.name)
                                                           or self.container_view_access(expr.name) is not None):
                _plain(expr, {"name", "is_last_use", "is_movable"})
                typ = self.bound(expr.name).type
                # An `Own[...]` parameter's name keeps the wrapper THIR gave the
                # parameter; the storage it names is the bare container.
                _require(expr, native_container_subject(expr.result_type) == typ
                         and (expr.form in (th.Form.BORROW, th.Form.VALUE)
                              or expr.form is th.Form.STORAGE and self.owned_container_bound(expr.name)),
                         "container name type mismatch")
            case th.THIRFieldAccess():
                c = self.place(expr)
                _require(expr, c.shape_of().cls is MIRClass.CONTAINER, "container field needs container storage")
                return c
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr) and self.container_receiver(expr):
                return self.call_container(expr)
            case th.THIRMethodCall() if self.container_receiver(expr):
                return self.method_call(expr, view=True)
            case _:
                raise MIRUnsupported(expr, "container source needs a place")
        # A binding is its own holder.
        return self.place(expr)

    def call_container(self, expr: th.THIRCall | th.THIRMethodCall) -> Fragment:
        """A container a user call returns by reference: a place of the
        callee's container (its referents are the summary's return
        origins), at the access the call's result is bound at."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        call = self.call(expr)
        result = self.call_results[expr]
        _require(expr, result is not None and native_container_type(result.type)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == result.type,
                 "call container result mismatch")
        # A place's operands (an index, slice bounds) are evaluated
        # before its container, so the call must not write what they read.
        _require(expr, not self.call_writes(expr), "order-sensitive eager operands")
        self.writes[expr] = False
        c = self.fragment()
        holder = c.lend(call)
        c.value = MIRValue(MIRPlace(holder), loc=expr.loc, held=holder)
        return c

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
        _plain(expr, {"receiver", "index", "bounds_safe", "bound_getitem"})
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
        """The binding a record reference names. With `qualified`, the
        receiver of an explicit ancestor field (`Base.n` in a method) may be
        spelled at an ancestor of the receiver's type."""
        match expr:
            case th.THIRName():
                _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
                name = expr.name
            case th.THIRSelf():
                _plain(expr, {"deref", "is_last_use", "is_movable", "pointer"})
                # A frame's reference receiver is not the pointer `this`.
                _require(expr, expr.pointer, "unsupported metadata: pointer")
                _require(expr, self.fn.receiver is not None, "missing receiver fact")
                _require(expr, expr.form is th.Form.BORROW, "receiver read form")
                _require(expr, not isinstance(unwrap_ref_type(expr.result_type), ReadonlyType)
                         or self.fn.receiver.readonly, "receiver read increases access")
                name = "self"
            case _:
                raise MIRUnsupported(expr, "reference needs local name")
        held = self.held(name)
        _require(expr, held is not None, "unknown reference source")
        named = unwrap_readonly(unwrap_ref_type(expr.result_type))
        if qualified and isinstance(expr, th.THIRSelf) and named != held.type:
            _require(expr, isinstance(named, NominalType) and self.binds_at(expr, held.type, named),
                     "reference type mismatch")
            return name
        self.reference(expr, held, expr.result_type)
        return name

    def record_source(self, expr: th.THIRExpr) -> Fragment:
        """The record storage an expression names in place, at the access
        it is reached with, as that place: a name or `self` (what its holder
        reaches; `held` is that holder), or a member read through them
        (`ln.a`, `self.line.a`). Only a copy, a returned borrow or a lent
        argument takes a member; reseats, aliases and wrapper captures keep
        needing a name (`reference_name`)."""
        kind = self.admitted_place(expr, _RECORD_PLACES)
        if kind is _Place.FIELD:
            _require(expr, expr.form is th.Form.STORAGE, "record member read form")
            s = self.place(expr)
            _require(expr, s.shape_of().cls is MIRClass.RECORD, "record source needs record storage")
            return s
        if kind is _Place.NAME and self.wraps(expr.name, MIRClass.OPTIONAL):
            # A narrowed Optional name: the record its payload points at.
            source = self.optional_name(expr, extract=True).payload
            _require(expr, isinstance(source, th.THIRBorrowedRecord), "record source needs record storage")
        else:
            # Any other source refuses here: a record source needs a name.
            self.reference_name(expr)
        # A name's own holder points at it and lends it.
        return self.place(expr)

    def borrowed_expression(self, expr: th.THIRExpr, result: MIRShape) -> Fragment:
        """A borrowed result, as the holder slot it is; `result` is the shape
        of a holder of it."""
        with self.argument_order(expr):
            if isinstance(expr, th.THIRFieldAccess):
                # A member of record storage the result reaches, borrowed in
                # place (THIR reads it in STORAGE form, as a storage borrow's
                # source); never lent with more access than its path has.
                self.reference(expr, result, expr.result_type)
                source = self.record_source(expr)
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
                name = self.reference_name(expr)
                source = self.held(name)
                _require(expr, not source.readonly or result.readonly, "return increases access")
                return self.valued(MIRValue(self.slot_of(name), loc=expr.loc))

    def borrowed_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> Fragment:
        if declaration:
            allowed = {"name", "resolved_type", "init", "cpp_type", "is_const"}
            if isinstance(stmt, th.THIRVarDecl):
                allowed.add("cpp_local_representation")
                _require(stmt, stmt.form is th.Form.BORROW, "call binding needs borrowed form")
            else:
                allowed.add("kind")
                _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported call binding")
            _plain(stmt, allowed)
            fact = _declared_borrow(stmt)
            self.reference(stmt, fact, stmt.resolved_type)
            holder = _loan(fact)
            source = stmt.init
        else:
            _plain(stmt, {"name", "kind", "value"})
            _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR and stmt.name not in self.parameters,
                     "unsupported call reseat")
            holder = self.held(stmt.name)
            _require(stmt, holder is not None, "missing call reseat destination")
            source = stmt.value
        value = self.borrowed_expression(source, holder)
        if declaration:
            slot = self.bind(stmt.name, holder, loc=stmt.loc).slot
        else:
            _require(stmt, stmt.name not in self.fixed_owned, "fixed owned local reseated")
            slot = self.slot_of(stmt.name)
        return self.put_borrowed_binding(stmt.loc, slot, declaration, value, stmt)

    def borrow_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> object:
        storage = stmt.storage_borrow is not None
        alias = stmt.alias_binding
        _require(stmt, (storage and alias is None) or isinstance(alias, th.THIRAliasBinding),
                 "missing or conflicting borrow binding")
        fact = stmt.storage_borrow if storage else alias.reference
        _require(stmt, isinstance(fact, th.THIRBorrowedRecord), "invalid borrow binding")
        operation = "storage_borrow" if storage else "alias_binding"
        if declaration:
            allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", operation}
            if isinstance(stmt, th.THIRVarDecl):
                allowed.add("cpp_local_representation")
                _require(stmt, stmt.form is th.Form.BORROW, "alias declaration form")
            else:
                allowed.add("kind")
                _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported alias declaration")
            _plain(stmt, allowed)
            name, source = stmt.name, stmt.init
            self.reference(stmt, fact, stmt.resolved_type)
            _require(stmt, fact == _declared_borrow(stmt), "alias access mismatch")
        elif isinstance(stmt, th.THIRAssign):
            _plain(stmt, {"target", "value", operation})
            name = self.reference_name(stmt.target)
            source = stmt.value
        else:
            _plain(stmt, {"name", "kind", "value", operation})
            _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported alias reseat")
            name, source = stmt.name, stmt.value
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, source is not None, "missing alias source")
        if isinstance(source, th.THIRFormConvert):
            _plain(source, {"value", "is_const"})
            _require(source, source.form is th.Form.BORROW
                     and source.is_const == fact.readonly,
                     "unsupported alias conversion")
            self.reference(source, fact, source.result_type)
            source = source.value
        tuple_member = False
        if storage:
            if isinstance(source, th.THIRSubscript):
                _require(source, declaration and isinstance(source.receiver, th.THIRName)
                         and source.receiver.name in self.parameters, "tuple borrow needs parameter capture")
                reference, place = self.projection(source, capture=True)
                tuple_member = True
            else:
                _require(source, isinstance(source, th.THIRFieldAccess) and source.form is th.Form.STORAGE,
                         "storage borrow needs record field")
                reference, place = self.field(source)
            _require(source, reference.cls is MIRClass.RECORD, "storage borrow needs record")
        else:
            source_name = self.reference_name(source)
            _require(stmt, alias.source == source_name, "alias source mismatch")
            reference = self.held(source_name)
            place = None
        self.reference(stmt, fact, reference.type)
        _require(stmt, not reference.readonly or fact.readonly, "alias increases access")
        if declaration:
            slot = self.bind(name, _loan(fact), loc=stmt.loc).slot
        else:
            _require(stmt, self.holds(name, fact), "alias destination mismatch")
            _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")
            slot = self.slot_of(name)
        if not storage:
            value = self.valued(MIRValue(self.slot_of(source_name), loc=source.loc))
            return self.put_borrowed_binding(stmt.loc, slot, declaration, value, stmt)
        # A tuple member a parameter captures points at the record.
        return self.put_borrow_binding(stmt.loc, slot, declaration, place, tuple_member, stmt)

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
        shape = fact_shape(typ, fact)
        _require(node, isinstance(shape, MIRShape), shape)
        self.register(node, shape, allow_owned=allow_owned)
        return shape

    def union_name(self, expr: th.THIRName) -> th.THIRUnionLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "union_read", "indirect"})
        fact = expr.union_read
        _require(expr, isinstance(fact, th.THIRUnionLayout) and self.union_is(expr.name, fact),
                 "missing or inconsistent union read")
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == fact.type,
                 "union read type or form mismatch")
        return fact

    def union_source(self, expr: th.THIRExpr, target: th.THIRUnionLayout,
                     literal: th.THIRUnionLiteral | None = None) -> Fragment:
        """What a union binding takes, engaging the alternative it chooses."""
        node = expr
        f = self.fragment()
        shape = value_shape(target.type, facts=target)
        if literal is not None:
            _require(expr, isinstance(literal, th.THIRUnionLiteral) and literal.layout == target
                     and type(literal.alternative) is int and 0 <= literal.alternative < len(target.elements),
                     "invalid union literal fact")
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS, "unsupported union literal coercion")
                expr = expr.expr
            _require(expr, isinstance(expr, th.THIRLiteral), "union literal fact needs literal source")
            _plain(expr, {"value", "int_cpp", "none_cpp"})
            member = target.elements[literal.alternative]
            _require(expr, type(expr.value) is type(literal.value) and expr.value == literal.value
                     and (member is None and literal.value is None
                          or not isinstance(member, th.THIRBorrowedRecord) and leaf_constant(member, literal.value)),
                     "union literal payload mismatch")
            source = None if member is None else MIRPlace(f.leaf(member, MIRConstant(literal.value), node.loc))
            f.value = MIRValue(MIRUnionConstruct(literal.alternative, source), shape, node.loc)
            return f
        if isinstance(expr, th.THIRName) and self.wraps(expr.name, MIRClass.UNION):
            source = self.union_name(expr)
            _require(expr, source.type == target.type, "union copy type mismatch")
            for src, dst in zip(source.elements, target.elements):
                if src is None or dst is None:
                    _require(expr, src is dst, "union copy absence mismatch")
                else:
                    self.payload_compatible(expr, thir_member(src), thir_member(dst))
            f.value = MIRValue(MIRUnionCopy(MIRPlace(self.slot_of(expr.name))), shape, node.loc)
            return f
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            _require(expr, None in target.elements, "union has no absence alternative")
            f.value = MIRValue(MIRUnionConstruct(target.elements.index(None)), shape, node.loc)
            return f
        if isinstance(expr, th.THIRName) and self.held(expr.name) is not None:
            name = self.reference_name(expr)
            source = self.held(name)
            matches = [i for i, m in enumerate(target.elements)
                       if isinstance(m, th.THIRBorrowedRecord) and m.type == source.type]
            _require(expr, len(matches) == 1, "union has no matching record alternative")
            self.payload_compatible(expr, source, thir_member(target.elements[matches[0]]))
            f.value = MIRValue(MIRUnionConstruct(matches[0], MIRPlace(self.slot_of(name))), shape, node.loc)
            return f
        plan = self.expr(expr)
        typ = plan.shape_of().type
        _require(expr, typ in target.elements and not self.writes[expr], "unsupported union member construction")
        f.value = MIRValue(MIRUnionConstruct(target.elements.index(typ), MIRPlace(f.splice(plan))), shape, node.loc)
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
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == (member.type if reference else member)
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
        actual = unwrap_readonly(unwrap_ref_type(expr.result_type))
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
            f.value = MIRValue(MIROptionalConstruct(), shape, loc)
            return f
        node = expr
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            if isinstance(expr.result_type, OptionalType):
                # The same layout read at the literal's own Optional type.
                problem = shape_problem(replace(shape, type=unwrap_readonly(unwrap_ref_type(expr.result_type))),
                                        expr.result_type)
                _require(expr, problem is None, problem)
            else:
                _require(expr, isinstance(expr.result_type, (NoneType, VoidType)),
                         "optional absence type")
            f.value = MIRValue(MIROptionalConstruct(), shape, loc)
            return f
        if isinstance(expr, th.THIRName) and self.wraps(expr.name, MIRClass.OPTIONAL) and (
                expr.optional_read is not None and not expr.optional_read.extract):
            source = self.optional_name(expr, extract=False)
            self.payload_compatible(expr, thir_member(source.payload), payload)
            f.value = MIRValue(MIROptionalCopy(MIRPlace(self.slot_of(expr.name))), shape, loc)
            return f
        if isinstance(expr, th.THIRFormConvert) and isinstance(expr.value, th.THIRFieldAccess):
            # A holder of an Optional record field's inline payload (`optional_to_ptr`): a borrow, no copy.
            _plain(expr, {"value", "is_const"})
            source, place = self.field(expr.value)
            _require(expr, source.cls is MIRClass.OPTIONAL and expr.form is th.Form.BORROW
                     and shape.record_payload and expr.is_const == payload.readonly, "unsupported optional field borrow")
            self.payload_compatible(expr, inline_payload_holder(source, self.layouts()), payload)
            f.value = MIRValue(MIROptionalCopy(f.splice(place)), shape, loc)
            return f
        if shape.record_payload:
            _require(expr, isinstance(expr, th.THIRName), "optional capture needs local name")
            name = self.reference_name(expr)
            source = self.held(name)
            _require(expr, source is not None and member_compatible(source, payload),
                     "optional capture access mismatch")
            f.value = MIRValue(MIROptionalConstruct(MIRPlace(self.slot_of(name))), shape, loc)
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
            f.value = MIRValue(MIROptionalConstruct(MIRPlace(payload_slot)), shape, loc)
            return f
        plan = self.expr(expr, payload.type)
        _require(expr, plan.shape_of().type == payload.type and not self.writes[expr],
                 "effectful or mistyped optional payload")
        f.value = MIRValue(MIROptionalConstruct(MIRPlace(f.splice(plan))), shape, loc)
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
        copy.value = MIRValue(MIRTupleCopy(copy.lend(value)), self.bound(name), loc)
        return self.whole(copy, loc)

    def tuple_expr(self, expr: th.THIRExpr, *, allow_owned: bool = False) -> Fragment:
        """A tuple value: a copy of a binding's, or one built from its
        elements -- each in its member's form: a record a borrowed member
        names (a binding's slot), a record an owned member builds, else a
        value; `holder` is the shape of a slot holding it."""
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW, th.Form.STORAGE), "unsupported tuple form")
        t = self.fragment()
        elements: list[object] = []
        if isinstance(expr, th.THIRName):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, self.wraps(expr.name, MIRClass.TUPLE), "tuple needs local payload")
            bound = self.bound(expr.name)
            layout = bound.members
            problem = shape_problem(bound, expr.result_type)
            _require(expr, problem is None, problem)
            self.tuple_members(expr, bound, allow_owned=allow_owned)
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
                    elements.append(t.splice(self.record_value(element, member.type)))
                elif isinstance(member, th.THIRBorrowedRecord):
                    _require(expr, isinstance(expr, th.THIRBorrowTupleLiteral), "value tuple borrows record")
                    name = self.reference_name(element)
                    source = self.held(name)
                    _require(element, source is not None and member_compatible(source, thir_member(member)),
                             "tuple capture type or access mismatch")
                    elements.append(self.slot_of(name))
                else:
                    plan = self.expr(element, member)
                    _require(element, plan.shape_of().type == member and not self.writes[element],
                             "effectful or mistyped tuple element")
                    elements.append(t.splice(plan))
        _require(expr, expr.form is th.Form.STORAGE if holder.owned_tuple and isinstance(expr, th.THIRName)
                 else expr.form in (th.Form.VALUE, th.Form.BORROW), "tuple form disagrees with layout")
        t.value = MIRValue(MIRTupleCopy(self.slot_of(expr.name)) if isinstance(expr, th.THIRName)
                           else MIRTupleConstruct(tuple(elements)), holder, expr.loc)
        return t

    def projection(self, expr: th.THIRSubscript, *, capture: bool = False) -> tuple[MIRShape, Fragment]:
        """A tuple member read in place: its member shape, and its place."""
        _plain(expr, {"receiver", "index", "tuple_index", "elem_pointer"}
               | ({"deref"} if capture else set()))
        _require(expr, not capture or expr.deref, "tuple capture needs dereferenced element")
        receiver = self.tuple_expr(expr.receiver, allow_owned=True)
        tuple_shape = receiver.shape_of()
        layout = tuple_shape.members
        _require(expr, not tuple_shape.owned_tuple or isinstance(expr.receiver, th.THIRName),
                 "owned tuple projection needs existing local")
        index = expr.tuple_index
        _require(expr, type(index) is int and 0 <= index < len(layout),
                 "missing or invalid tuple index")
        _require(expr, isinstance(expr.index, th.THIRLiteral)
                 and type(expr.index.value) is int and expr.index.value == index,
                 "tuple index disagreement")
        _plain(expr.index, {"value", "int_cpp"})
        member = layout[index]
        # The render's pointer fact must agree with the layout MIR reads.
        _require(expr, not expr.elem_pointer
                 or member.cls is MIRClass.RECORD and member.is_pointer,
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
        _require(expr, not expr.opt_deref_check, "unproven optional field read")
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
                    and unwrap_readonly(unwrap_ref_type(receiver.result_type)) == reference.type)), reason)
        _require(expr, isinstance(fact, th.THIRFieldIdentity), "missing field identity")
        # An identity names a declared member of any type, not only one MIR models.
        _require(expr, position_problem(fact.type, MIRPosition.FIELD) is None, "unsupported field type or form")
        # A field of a derived record is a member of its layout, keyed by its
        # declaring owner; the layout comes along so a shadowed name is seen
        # under both owners. A flat record's own field needs no layout, and a
        # derived record's own field only when its definition verified (an
        # inherited field of an unverified one refuses with its reason).
        definition = self.definitions.records.get(reference.type)
        derived = isinstance(definition, MIRConstructorDefinition) and bool(definition.layout.ancestors)
        layout = (self.record_layout(expr, reference.type)
                  if derived or fact.owner != reference.type else None)
        _require(expr, isinstance(fact.owner, NominalType) and bool(fact.name) and (
            fact.owner == reference.type if layout is None
            else MIRField(MIRFieldId(fact.owner, fact.name), fact.type) in layout.fields), "field owner mismatch")
        shape = member_shape(fact.type, reference.readonly, self.layouts())
        readonly = shape.readonly
        _require(expr, not (write and readonly), "readonly field store")
        member_field = MIRField(MIRFieldId(fact.owner, fact.name), fact.type)
        if shape.cls is MIRClass.CONTAINER:
            # A container field is owned storage of the record; its region is
            # reached through it, and it is never replaced whole.
            _require(expr, not write, "container field replacement is unsupported")
            bare = unwrap_readonly(fact.type)
            _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == bare
                     and expr.form in (th.Form.STORAGE, th.Form.BORROW), "unsupported field type or form")
            self.register_container(expr, bare)
        elif shape.cls is MIRClass.OPTIONAL:
            # The payload record is stored inline: copied, destroyed and
            # replaced with the field, so its verified definition comes along.
            self.record_layout(expr, payload)
            _require(expr, expr.form is th.Form.STORAGE, "unsupported field type or form")
            if expr.narrowed_deref:
                _require(expr, not write, "optional field payload write")
                self.reference(expr, th.THIRBorrowedRecord(payload, readonly), expr.result_type)
                # A narrowed Optional field read names its inline payload.
                shape = step(shape, MIROptionalPayload(), self.layouts())
            else:
                # An assign target keeps the narrowed type but writes the whole field.
                result = unwrap_readonly(unwrap_ref_type(expr.result_type))
                _require(expr, result == unwrap_readonly(fact.type) or write and result == payload,
                         "unsupported field type or form")
        elif shape.cls is MIRClass.VIEW:
            # A view member stores a loan in the record object, keyed by a
            # member of the record's layout: read whole, or rebound whole
            # (`view_member_write`).
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.BORROW,
                     "unsupported field type or form")
            self.held_layout(expr, reference.type)
            self.held_root(expr)
        elif shape.cls is MIRClass.OWNED_LEAF:
            # The field is a place of the record's storage: its buffer is
            # borrowed, copied out or replaced in place, never projected into.
            # Its form is how the C++ spells the member (a BigInt reads as a
            # value scalar, str as storage); either way it is that lvalue.
            _require(expr, expr.result_type == fact.type and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                     "unsupported field type or form")
            self.leaf_layout(expr, fact.type)
        else:
            # A readonly-wrapped owned leaf has no field place of its own type.
            _require(expr, owned_value_type(fact.type) is None, "owned-leaf record field")
            if shape.cls is MIRClass.LEAF:
                _require(expr, expr.result_type == fact.type and expr.form is th.Form.VALUE,
                         "unsupported field type or form")
            else:
                # An inline record member: a place of the record's storage,
                # borrowed, copied out, or replaced whole in place (`member_write`).
                self.reference(expr, th.THIRBorrowedRecord(unwrap_readonly(fact.type), readonly), expr.result_type)
        f = self.fragment()
        raises = base.value.raises
        root = f.splice(base)
        f.value = MIRValue(MIRPlace(root.root, root.projections + f.storage_steps(root) + (member_field,)
                                    + ((MIROptionalPayload(),) if expr.narrowed_deref else ())),
                           loc=expr.loc, raises=raises)
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
        value = self.record_value(expr, fact.type, temporary=True, call=not isinstance(expr, th.THIRCtorCall))
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
                _plain(expr, {"variant_cpp", "member_cpps", "union_test"})
                fact = expr.union_test
                _require(expr, typ == BOOL and isinstance(fact, th.THIRUnionTest)
                         and self.union_is(fact.source, fact.layout)
                         and bool(fact.alternatives) and len(set(fact.alternatives)) == len(fact.alternatives)
                         and all(type(i) is int and 0 <= i < len(fact.layout.elements)
                                 for i in fact.alternatives), "missing or invalid union test")
                result = self.is_alternative(f, fact.source, fact.alternatives, False, loc)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                call = self.method_call(expr)
                result = f.leaf(typ, f.splice(call), loc)
                writing = self.call_writes(expr)
            case th.THIRSubscript() if expr.tuple_index is None:
                _plain(expr, {"receiver", "index", "bounds_safe"})
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
                result = self.is_alternative(f, expr.operand.name, (layout.elements.index(None),), expr.negate, loc)
            case th.THIRIsNone() if isinstance(expr.operand, th.THIRFieldAccess):
                _plain(expr, {"operand", "negate", "value_repr"})
                # An Optional record field tested in place: `std::optional` storage.
                whole, place = self.field(expr.operand)
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and whole.cls is MIRClass.OPTIONAL and expr.value_repr is True,
                         "unsupported optional field test")
                result = self.is_present(f, f.splice(place), expr.negate, loc)
            case th.THIRIsNone():
                _plain(expr, {"operand", "negate", "value_repr"})
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and isinstance(expr.operand, th.THIRName), "unsupported optional test")
                layout = self.optional_name(expr.operand, extract=False)
                _require(expr, expr.value_repr is (not isinstance(layout.payload, th.THIRBorrowedRecord)),
                         "optional test representation mismatch")
                result = self.is_present(f, MIRPlace(self.slot_of(expr.operand.name)), expr.negate, loc)
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
            case _:
                raise MIRUnsupported(expr, "unsupported expression")
        f.value = MIRValue(result, loc=loc)
        self.writes[expr] = writing
        return f

    def is_alternative(self, f: Fragment, source: str, alternatives: tuple[int, ...], negate: bool,
                       loc: object) -> MIRSlotId:
        found = f.leaf(BOOL, MIRIsAlternative(MIRPlace(self.slot_of(source)), alternatives), loc)
        return f.leaf(BOOL, MIRNot(found), loc) if negate else found

    def is_present(self, f: Fragment, place: MIRPlace, negate: bool, loc: object) -> MIRSlotId:
        """`negate` is THIR's `is not None`: presence itself."""
        present = f.leaf(BOOL, MIRIsPresent(place), loc)
        return present if negate else f.leaf(BOOL, MIRNot(present), loc)

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
                _require(stmt, not self.borrowed_container(name) and name not in self.iteration_references,
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
                _require(stmt, backing is not None and backing.record_storage is MIRRecordStorageKind.OPTIONAL
                         and self.holds(name, fact)
                         and self.bound(name).type == stmt.target.result_type == fact.type,
                         "optional record backing mismatch")
                value = self.record_value(stmt.value, fact.type)
                _require(stmt, self.records[fact.type].layout.movable, "optional backing needs movable record")
                return self.put_optional_record_assign(stmt.loc, bound.slot, bound.backing, value)
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
                                    self.union_source(stmt.value, stmt.union_layout, stmt.union_literal),
                                    self.bound(name).scalar_wrapper)
            case th.THIRAssign() if stmt.rebind_storage is not None:
                return self.replacement(stmt)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                return self.borrow_binding(stmt)
            case th.THIRPtrLocalRebind() if _user_call(stmt.value) or isinstance(stmt.value, th.THIRIfExpr):
                return self.borrowed_binding(stmt)
            case th.THIRNoOpStmt():
                _plain(stmt, set())
                return None
            case th.THIRSetItem():
                return self.element_write(stmt)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.owned_container_bound(stmt.target.name)):
                # Rebuilding owned container storage: a replacement of the whole container.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                typ = self.bound(stmt.target.name).type
                _require(stmt, stmt.target.name not in self.parameters, "owned container parameter reseat")
                return self.put_assign(stmt.loc, self.name_place(stmt.target.name, stmt.target.loc), self.full_expression(
                    stmt.value, check=lambda: self.container_value(stmt.value, typ)), True)
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
                return self.put_assign(stmt.loc, self.name_place(name, stmt.target.loc), checked, False)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.view_bound(stmt.target.name)):
                # Reseating a view holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                return self.put_assign(stmt.loc, self.name_place(name, stmt.target.loc), self.full_expression(
                    stmt.value, check=lambda: self.view_value(stmt.value, self.bound(name).type)), False)
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
                return self.put_assign(stmt.loc, self.name_place(target.name, target.loc), value, True)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                     and owned_value_type(stmt.target.result_type) is not None):
                # Replaces the field's buffer in place: a write event on the field place.
                _plain(stmt, {"target", "value"})
                shape, place = self.field(stmt.target, write=True)
                value = self.full_expression(stmt.value, check=lambda: self.owned_sink(stmt.value))
                _require(stmt, shape.cls is MIRClass.OWNED_LEAF and value.shape_of().type == shape.type,
                         "assignment type mismatch")
                return self.put_assign(stmt.loc, place, value, True)
            case th.THIRAssign() if _optional_field_target(stmt.target):
                return self.optional_field_write(stmt)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                     and record_type(unwrap_readonly(unwrap_ref_type(stmt.target.result_type)))):
                return self.member_write(stmt)
            case th.THIRAssign() if isinstance(stmt.target, th.THIRFieldAccess) and view_leaf(stmt.target.result_type):
                return self.view_member_write(stmt)
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
                                           self.tuple_copy(value, stmt.target.name), False)
                _require(stmt, isinstance(stmt.target, (th.THIRName, th.THIRFieldAccess)),
                         "assignment needs local or field target")
                _require(stmt, not isinstance(stmt.target, th.THIRName) or not self.payload_alias(stmt.target.name),
                         "write through scalar payload alias")
                if isinstance(stmt.target, th.THIRFieldAccess):
                    shape, place = self.field(stmt.target, write=True)
                    target_type = shape.type
                else:
                    place = self.valued(MIRValue(self.scalar_name_place(stmt.target), loc=stmt.target.loc))
                    target_type = self.types[stmt.target]
                value = self.full_expression(stmt.value, expected=target_type)
                _require(stmt, not (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None
                                   and value.needs_region), "temporary global assignment")
                _require(stmt, target_type == value.shape_of().type, "assignment type mismatch")
                return self.put_scalar_assign(stmt.loc, place, value)
            case th.THIRExprStmt():
                _plain(stmt, {"expr", "void_cast"})
                return self.put_expression(stmt.loc, self.full_expression(stmt.expr, discard=True, at=stmt.loc))
            case th.THIRReturn():
                return self.return_(stmt)
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
                            f.end(MIRGoto(bridge.id, loc))
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
                        f.end(MIRGoto(head.id, loc))
                    f.current = normal
                    self.scoped_into(f, orelse)
                if f.current is not None:
                    f.end(MIRGoto(after.id, loc))
                f.current = after
                return f
            case th.THIRBreak() | th.THIRContinue():
                _plain(stmt, set())
                _require(stmt, loops > 0, "loop control outside loop")
                return self.put_jump(stmt.loc, isinstance(stmt, th.THIRContinue))
            case th.THIRParamCopy():
                # The body's owned copy of a borrowed parameter it reassigns; reads see the copy.
                _plain(stmt, {"name", "cpp_type", "init_cpp"})
                _require(stmt, any(s is stmt for s in self.fn.body) and stmt.name in self.parameters
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
        (loc, hoists, _, condition, then, _), = arms
        self.place_hoists(f, hoists, loc)
        cond = f.full_expression_value(condition)
        yes, no = f.block(), f.block()
        f.branch(cond, yes.id, no.id, loc)
        exits = []
        for block, arm in ((yes, then), (no, orelse)):
            f.current = block
            self.scoped_into(f, arm)
            if f.current is not None:
                exits.append(f.current)
        if exits:
            join = f.block()
            for block in exits:
                f.current = block
                f.end(MIRGoto(join.id, loc))
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
               self.temp_scope(stmt, "condition"))
        return [arm, *rest], orelse, has_else

    def planned_if(self, f: Fragment, arms: list[tuple], orelse: list[Fragment], has_else: bool) -> None:
        self.place_hoists(f, arms[0][1], arms[0][0])
        join = f.block()

        def arm(index: int) -> None:
            loc, hoists, temporaries, condition, then, _ = arms[index]
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
                f.end(MIRGoto(join.id, loc))
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
                    f.end(MIRGoto(join.id, loc))

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
            actual = unwrap_readonly(unwrap_ref_type(stmt.target.result_type))
            _require(stmt, actual in (typ, self.bound(name).type) or isinstance(actual, NoneType),
                     "optional destination type mismatch")
        if isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is not None:
            member = stmt.optional_layout.payload
            _require(stmt, isinstance(member, th.THIRBorrowedRecord)
                     and stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                     "unsupported optional replacement storage")
            _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE or not member.readonly,
                     "readonly in-place replacement")
            value = self.record_value(stmt.value, member.type)
            _require(stmt, self.records[member.type].layout.movable, "replacement needs movable record")
            own = stmt.rebind_storage is RebindStorage.OWN
            backing = (stmt.value.loc, value_shape(member.type), _loan(member)) if own else None
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
        if stmt.value is None:
            _require(stmt, isinstance(self.fn.return_type, VoidType), "missing return value")
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
        if self.container_result is not None:
            # A container place returned by reference: a borrow of it.
            _require(stmt, self.container_receiver(stmt.value), "container return needs a place")
            place = self.container_place(stmt.value)
            shape = place.shape_of()
            _require(stmt, shape.type == self.container_result.type
                     and (not shape.readonly or self.container_result.readonly), "return increases access")
            f.end(MIRReturn(f.splice(self.holding(place, raises=False)), loc))
            return f
        if result is not None:
            value = f.splice(self.borrowed_expression(stmt.value, _loan(result)))
            # The return a borrow operation is published under.
            f.borrow_operation(stmt)
            f.end(MIRReturn(value, loc))
            return f
        moved: list[MIRSlotId | None] = [None]
        if (typ := self.owned_container_result) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_container_source(stmt.value, typ, moved))
            return self.owned_return(f, value, value_shape(typ), moved[0], loc, loc)
        if (typ := self.owned_record_result) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_record_source(stmt.value, typ, moved))
            return self.owned_return(f, value, value_shape(typ), moved[0], loc, loc)
        if (owned := owned_value_type(self.fn.return_type)) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_leaf_source(stmt.value, moved))
            _require(stmt, value.shape_of().type == owned, "return type mismatch")
            return self.owned_return(f, value, value_shape(owned), moved[0], loc, value.expression_loc)
        value = self.full_expression(stmt.value, expected=self.fn.return_type)
        _require(stmt, value.shape_of().type == self.fn.return_type, "return type mismatch")
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
                     loc: object, write_loc: object) -> Fragment:
        """An owned result: a binding's own storage (a record's backing, a
        container or owned-leaf binding itself) leaves the body itself --
        the move is C++'s (or elided), not an event of the body; anything
        else is copied or built into a result slot."""
        if moved is not None:
            f.end(MIRReturn(moved, loc))
            return f
        dest = f.slot(result, storage_duration=f.scoped_duration())
        with f.full_expression(value):
            f.write(dest, f.splice(value), write_loc, f.owned_initialization(dest))
        f.end(MIRReturn(dest, loc))
        return f

    def owned_record_source(self, expr: th.THIRExpr, typ: NominalType, moved: list) -> Fragment:
        """What an `Own[R]` record result takes: the backing of a fixed owned
        local moved out (C++ moves or elides it), or a result built for it."""
        source = expr.value if isinstance(expr, th.THIRMove) else expr
        if isinstance(source, th.THIRName) and source.global_binding is None:
            # A reseat may retarget the holder at another backing: the local's own may be stale.
            _require(expr, source.name not in self.owned_records or source.name in self.fixed_owned,
                     "reassigned local returned")
        if isinstance(expr, th.THIRName) and expr.global_binding is None:
            _require(expr, expr.name in self.fixed_owned, "owned record return needs fixed owned local")
            _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
            # A readonly holder cannot be moved from: C++ would copy it.
            _require(expr, not self.held(expr.name).readonly, "readonly local returned")
            _require(expr, self.held(expr.name).type == typ, "return type mismatch")
            self.writes[expr] = False
            moved[0] = self.scope[expr.name].backing
            return self.fragment()
        return self.record_value(expr, typ, call=True)

    def owned_leaf_source(self, expr: th.THIRExpr, moved: list) -> Fragment:
        """What an owned-leaf result takes: the body's own storage of a
        binding moved out (C++ moves it), or a copy or a build."""
        plan = self.owned_sink(expr)
        moved[0] = plan.value.storage
        return plan

    def owned_container_source(self, expr: th.THIRExpr, typ: NominalType, moved: list) -> Fragment:
        """What an `Own[...]` container result takes: the body's own
        container storage moved out, or a literal built for it."""
        if isinstance(expr, th.THIRName) and expr.global_binding is None and self.owned_container_bound(expr.name):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, self.bound(expr.name).type == typ and expr.form in (th.Form.BORROW, th.Form.STORAGE),
                     "return type mismatch")
            moved[0] = self.slot_of(expr.name)
            return self.fragment()
        return self.container_value(expr, typ)

    def element_write(self, stmt: th.THIRSetItem) -> Fragment:
        """`xs[i] = v`: replaces an element in place -- a weak update of the
        container's element region -- when the receiver's `__setitem__` stub
        declares it writes elements (`mutates="elements"`: it moves no
        element) or writes through a view, which cannot change its source's
        shape; otherwise the `__setitem__` call it is, a structure write of
        the receiver derived from the stub like any container method."""
        _plain(stmt, {"target", "value", "stub_callee"})
        callee = stmt.stub_callee
        _require(stmt, isinstance(callee, th.THIRStubCallee) and callee.receiver, "element write needs a stub contract")
        target = stmt.target
        _require(stmt, isinstance(target, th.THIRSubscript) and self.container_receiver(target.receiver),
                 "element write needs a container place")
        if not (callee.mutates_elements is True or container_view(callee.signature.param_types[0])):
            return self.put_expression(stmt.loc, self.full_expression(
                stmt.value, check=lambda: self.setitem_call(stmt, callee)))

        def check() -> Fragment:
            element = self.element_access(target)
            member = element.shape_of()
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
                    value = self.record_value(stmt.value, member.type, call=True)
            self.argument_order_rule(stmt, (target.index, stmt.value))
            # `xs[i] = v`: the value in the member's form (Python evaluates
            # it first), then the index, replaces the element region in
            # place under the container's root.
            f = self.fragment()
            stored = f.splice(value)
            place = f.splice(element)
            f.write(place, stored, stmt.loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, place.root))
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
                 and isinstance(callee.signature.return_type, VoidType), "call signature mismatch")
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
                f.end(MIRGoto(advance.id, loc))
            f.current = advance
            f.write(counter, MIRRangeAdvance(counter, step), loc)
            f.end(MIRGoto(head.id, loc))
        f.current = normal
        self.scoped_into(f, orelse)
        if f.current is not None:
            f.end(MIRGoto(after.id, loc))
        f.current = after
        return f

    def native_loop(self, stmt: th.THIRForEach, loops: int) -> Fragment:
        _plain(stmt, {"var", "elem_type", "iterable", "body", "const_loop_var", "iterable_lvalue",
                      "orelse", "hoist_loop_var", "hoist_decls", "hoisted_bindings", "iteration"})
        fact = stmt.iteration
        # A container view a stub returns, or a container a user call returns
        # by reference, is a call result the loop holds.
        view = isinstance(stmt.iterable, (th.THIRCall, th.THIRMethodCall))
        _require(stmt, isinstance(fact, th.THIRNativeIteration) and (stmt.iterable_lvalue is True or view)
                 and type(stmt.const_loop_var) is bool and type(stmt.hoist_loop_var) is bool
                 and isinstance(stmt.var, str) and bool(stmt.var), "missing or invalid native iteration facts")
        # Any container place: a borrowed, owned or viewed binding, a field, or a view a stub returns.
        place = self.container_place(stmt.iterable)
        shape = place.shape_of()
        source = fact.source
        _require(stmt, isinstance(source, th.THIRNativeContainer), "missing or invalid native iteration facts")
        # A cursor binds the source's declared element, one at a time.
        _require(stmt, binds_cursor(shape.type), "missing or invalid native iteration facts")
        element = shape.element
        _require(stmt, isinstance(source, th.THIRNativeContainer) and source.type == shape.type
                 and source.readonly == shape.readonly
                 and source.element == (th.THIRBorrowedRecord(element.type, element.readonly)
                                        if element.cls is MIRClass.RECORD else element.type),
                 "native iteration source disagrees with binding")
        # A record element is bound over its container's region, at the
        # access the element has there; a write through a loop variable
        # bound readonly is a store through readonly storage, which the
        # validator refuses.
        reference = (th.THIRBorrowedRecord(element.type, loan_access(
            element.type, MIRClass.RECORD, Local(const=stmt.const_loop_var, source_readonly=element.readonly)))
                     if element.cls is MIRClass.RECORD else None)
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
        if reference or view or element.cls is MIRClass.OWNED_LEAF:
            self.iteration_references.add(stmt.var)
        if stmt.hoist_loop_var:
            slot = self.slot_of(stmt.var)
        else:
            slot = self.bind(stmt.var, _loan(reference) if reference
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
            f.end(MIRGoto(advance.id, loc))
        f.current = advance
        f.write(cursor, MIRIteratorAdvance(cursor), loc)
        f.end(MIRGoto(head.id, loc))
        f.current = normal
        self.scoped_into(f, orelse)
        if f.current is not None:
            f.end(MIRGoto(after.id, loc))
        f.current = after
        return f

    def hoists(self, stmt: th.THIRIf | th.THIRWhile | th.THIRForRange | th.THIRForEach) -> list[tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None]]:
        facts = stmt.hoisted_bindings
        _require(stmt, all(isinstance(f, th.THIRHoistedBinding) for f in facts)
                 and tuple(f.name for f in facts) == tuple(decl.name for decl in stmt.hoist_decls),
                 "missing or inconsistent hoisted binding facts")
        hoisted: list[tuple[MIRSlotId, MIRSlotId | None, tuple[int, object] | None]] = []
        for fact in facts:
            _require(stmt, fact.name not in self.scope and fact.initially_assigned is False
                     and fact.placement is th.THIRStoragePlacement.SCOPE, "invalid hoisted binding placement or availability")
            laid = self.laid_out(stmt, fact.type, holder=fact, others=("optional_record_storage",))
            backing = None
            if fact.optional_record_storage is not None:
                reference = fact.optional_record_storage
                self.reference(stmt, reference, fact.type)
                # The storage backs an Optional's payload: one an Optional of the record holds.
                optional = OptionalType(reference.type)
                problem = shape_problem(value_shape(optional, facts=th.THIROptionalLayout(reference)), optional)
                _require(stmt, problem is None, problem)
                # The body's own backing storage, which assignments engage, and
                # its holder: at the storage's own access, never readonly.
                _require(stmt, not reference.readonly, "optional record backing must be mutable")
                definition = self.definitions.get(stmt, reference.type)
                _require(stmt, definition.layout.movable, "optional backing needs movable record")
                self.records[reference.type] = definition
                backing = value_shape(reference.type)
                shape = value_shape(reference.type, access=backing.readonly, role=MIRRole.LOAN)
            elif laid is not None:
                shape = laid
                _require(stmt, shape.cls is not MIRClass.UNION or shape.scalar_wrapper,
                         "unsupported hoisted union storage")
            else:
                _require(stmt, storage_leaf(fact.type), "unsupported hoisted value")
                shape = scalar_shape(fact.type)
            default = fact.physical_default
            if shape.scalar_wrapper:
                # A union defaults to its first alternative's zero, an Optional to absent.
                first = shape.alternatives[0] if shape.cls is MIRClass.UNION else None
                expected = None if first is None else zero_value_of(first.type)
                _require(stmt, (first is None or expected is not None)
                         and isinstance(default, th.THIRWrapperDefault)
                         and type(default.alternative) is int and default.alternative == 0
                         and type(default.value) is type(expected) and default.value == expected,
                         "missing or inconsistent physical wrapper default")
            else:
                _require(stmt, default is None, "physical default on non-wrapper hoist")
            # An Optional record's backing, empty until a branch assigns it.
            bound = self.bind(fact.name, shape, backing, loc=stmt.loc, backing_kind=MIRSlotKind.LOCAL,
                              backing_storage=MIRRecordStorageKind.OPTIONAL)
            hoisted.append((bound.slot, bound.backing,
                                  (default.alternative, default.value) if default is not None else None))
        return tuple(hoisted)

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
            function = assemble(mir, **lowering.assembly)
        except MIRRepeatedInitializationError as failure:
            raise MIRUnsupported(failure, str(failure)) from failure
        except (MIRPresenceError, MIRDefiniteAssignmentError, MIRFallthroughError,
                MIRRegionStorageError) as failure:
            raise MIRUnsupported(lowering.fn, str(failure)) from failure
        return MIRLoweredStorage(function, MappingProxyType(mir.backing_places),
                                 MappingProxyType(mir.borrow_operations))
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
    """Lower complete pure initialization before a supported constructor tail."""
    result = lower_constructor_storage(ctor, body, definitions=definitions, summaries=summaries)
    return result.function if isinstance(result, MIRLoweredStorage) else result


def lower_constructor_storage(ctor: th.THIRConstructor, body: MIRBodyId, *,
                              definitions: MIRDefinitions | None = None,
                              summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                              ) -> MIRLoweredStorage | MIRNotCovered:
    try:
        definitions = definitions if definitions is not None else MIRDefinitions()
        initialization = constructor_initialization(ctor, definitions.records)
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))
    fn = th.THIRFunction(
        f"{ctor.record_name}.__init__", ctor.params, VoidType(), ctor.body, th.THIRFunctionLayout(),
        # THIR's receiver fact for the record the constructor initializes, which
        # it writes; the binding derives its access from it (`parameter_binding`).
        receiver=th.THIRBorrowedRecord(initialization.layout.type, False), temp_plan=ctor.temp_plan)
    return _lower(_Lowering(fn, body, definitions, summaries, initialization, ctor))
