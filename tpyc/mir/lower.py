"""All-or-nothing lowering of scalar and record-storage THIR operations."""

from dataclasses import dataclass, replace
from contextlib import contextmanager
from collections.abc import Callable, Iterator, Mapping
from typing import NamedTuple
from types import MappingProxyType

from ..identity_map import IdentityMap
from ..codegen_cpp.forms import LoopBinding
from ..type_def_registry import is_array
from ..parse import ResultForm, RebindStorage
from ..thir import nodes as th
from ..thir.temp_plan import if_chain, validate_plan
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, container_view, converted_literal, declared_members, holds_loan, inline_member_type,
    leaf_constant, leaf_global, modeled_field, native_container_subject, native_container_type, optional_record_field,
    owned_constant, owned_leaf,
    owned_value_type, primitive_leaf, primitive_owned_leaf, readonly_elements, record_type, storage_leaf, value_class,
    view_compatible, view_leaf,
)
from ..type_def_registry import ParamPassing, int_traits_of, type_def_of, zero_value_of
from ..typesys import (
    BOOL, INT32, NominalType, TpyType,
    NoneType, OptionalType, ReadonlyType, Representation, TupleType, UnionType, VoidType,
    certified_primitive_comparison, holds_borrowing_view, is_void_like_type,
    return_representation, through_view, unwrap_own, unwrap_readonly, unwrap_ref_type, view_family_of, view_owned_leaf,
)
from .nodes import (
    MIRBodyId, MIRBodyKind, MIRField, MIRFieldId, MIRFunction, MIRGlobalId, MIRHolding, MIRMemberInitMode,
    MIRNotCovered, MIRPoint, MIRPlace, MIRRecordLayout, MIRShape, MIRStorageDuration, function_body_kind,
)
from .coverage import MIRUnsupported, literal_type, plain as _plain, require as _require, scalar_param
from .definitions import (
    MIRComposedConstruct, MIRConstructorDefinition, MIRContainerDefinition, MIRDefinitions, MIRFieldInitializer,
    MIRHeldLayout, MIROwnedLeafDefinition, constructor_initialization, optional_parameter,
    owned_parameter, owned_record_parameter, record_parameter,
)
from .emit import MIRFallthroughError, PlanEmitter
from .plan import (
    PAbsent, PAnchored, PAppend, PAssign, PBinding, PBody, PBorrowBinding, PBorrowedBinding, PBorrowedCall,
    PBorrowedField, PBorrowedName, PBorrowedSelect, PCall, PCallStatement, PCallValue, PCompare, PConditional,
    PConstant, PConstruct, PContainer, PContainerAlias, PContainerCall, PContainerField, PContainerLiteral,
    PContainerMove, PConvert, PDeclare, PElementPlace, PElementStore,
    PElementValue, PExpressionStatement, PFieldPlace, PForEach, PFullExpression, PGlobalPlace, PHoist,
    PIf, PIsAlternative, PIsPresent, PJump, PLentTemporary, PLogical, PMemberArgument, PMemberComposed,
    PMemberConstant, PMemberOptional, PMembers, PMemberView, PMoveOut, PNamePlace, PNarrowAlias, PNot, POperation,
    POptionalAssign, POptionalDeclare, POptionalFieldCopy, POptionalFieldValue, POptionalOperand,
    POptionalRecordAssign, POwnedArgument, POwnedCall, POwnedField, POwnedFormConvert, POwnedLiteral,
    POwnedPassthrough, POwnedPlace, POwnedRecordDeclare, POwnedReturn, PParameter, PParamCopy, PPrint,
    PPromote, PRangeLoop, PRead, PRecordBacking, PRecordCall, PRecordCopy, PRecordMove,
    PRecordSource, PRecordTemporary, PReplacement, PReturnBorrowed, PReturnHolder,
    PReturnValue, PScalarAssign, PSetItemCall, PSpanAlias, PSpanRegion, PSpanSlice, PStatementTemporaries,
    PTemporary, PTemporaryRecordPlace, PTupleAlias, POwnedTupleDeclare, PTupleElementPlace,
    PTupleLiteral, PTupleName, PUnionAssign, PUnionDeclare, PUnionPayloadPlace, PViewCall, PViewField,
    PViewMemberAssign, PViewName, PViewOf, PViewSlice, PWalrus, PWhile, PWrapperCapture, PWrapperCopy,
    PWrapperLiteral, PWrapperValue,
)
from .shapes import from_thir, with_access
from .call_contract import (
    BORROWING_PASSINGS, OWNING_PASSINGS, MIRCallSummary, MIRParameterBinding, MIRSummaryResult,
    MIRSummaryState, binds_at, borrowed_result_of, bound_result, container_result, optional_layout_problem,
    owned_record_result, path_hops, reference_problem, stub_protocol_argument, stub_summary, summary_problem,
    transfer_ends, view_result,
)
from .validate import MIRDefiniteAssignmentError, MIRPresenceError, MIRRepeatedInitializationError


def _literal(expr: th.THIRExpr) -> bool:
    return isinstance(expr, th.THIRLiteral) or (
        isinstance(expr, th.THIRCoerce) and isinstance(expr.expr, th.THIRLiteral))


def _user_call(expr: th.THIRExpr) -> bool:
    """A call `_Coverage.call` checks: a free-function call, or a method
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


def _region_view(typ: TpyType) -> bool:
    """A container view MIR holds as the elements region it views: one
    whose stub declares an element a cursor binds (a Span, a dict keys or
    values view, a varargs pack). An items view is only a call's result."""
    bare = unwrap_readonly(unwrap_ref_type(typ))
    return isinstance(bare, NominalType) and container_view(bare) and binds_cursor(bare)


def _view_holder_fact(typ: TpyType) -> th.THIRBorrowedRecord:
    """The holder fact of a view slot: a readonly borrow typed by the view."""
    return th.THIRBorrowedRecord(typ, True)


def _container_view_fact(typ: NominalType, readonly: bool) -> th.THIRBorrowedRecord:
    """The holder fact of a container view slot (a Span, a dict view): a
    borrow typed by the view, readonly when its elements are or when what
    it views is."""
    return th.THIRBorrowedRecord(typ, readonly or readonly_elements(typ))


def _slot_shape(typ: TpyType, members_of: Mapping[NominalType, tuple[MIRShape, ...]],
                reference: th.THIRBorrowedRecord | None = None, *, storage: bool = False, readonly: bool = False,
                tuple_layout: th.THIRTupleLayout | None = None,
                optional_layout: th.THIROptionalLayout | None = None,
                union_layout: th.THIRUnionLayout | None = None, alias: bool = False,
                container: th.THIRNativeContainer | None = None, iterator: bool = False,
                members: tuple[MIRShape, ...] = ()) -> MIRShape:
    """The shape of a slot holding a value of `typ` under these THIR facts
    (`shapes.from_thir`): a container or reference fact gives its own type
    and access, a wrapper layout the bare type; any other value has the
    access given, a payload alias is readonly. A container fact's members
    are its type's (`members_of`) under its access."""
    typ = container.type if container else reference.type if reference else (
        unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
        if optional_layout is not None or union_layout is not None or tuple_layout is not None else typ)
    access = reference.readonly if reference else container.readonly if container else readonly or alias
    return from_thir(typ, access, storage=storage, reference=reference, tuple_layout=tuple_layout,
                     optional_layout=optional_layout, union_layout=union_layout, alias=alias,
                     members=with_access(members_of[container.type], container.readonly) if container else members,
                     borrowed_container=container is not None, iterator=iterator)


# The THIR fact a binding is declared with that its reads are checked
# against: a wrapper's layout, a borrowed container, or the record an
# Optional backing holds.
_BindingFact = (th.THIROptionalLayout | th.THIRUnionLayout | th.THIRTupleLayout | th.THIRNativeContainer
                | th.THIRBorrowedRecord)


class _Field(NamedTuple):
    """A checked place: the fact of what it holds (a field's type, record,
    Optional layout or container; a tuple member), and its place plan."""
    fact: object
    place: object


@dataclass(frozen=True)
class _Bound:
    """A name in the check pass's scope: its binding's plan, and the type and
    THIR fact its declaration gives it, which reads of the name are checked
    against."""
    plan: PBinding
    declared: TpyType
    fact: _BindingFact | None = None


class _DeclaredTypes(Mapping[str, TpyType]):
    """Each name in scope with its declared type."""

    def __init__(self, scope: Mapping[str, _Bound]) -> None:
        self.scope = scope

    def __getitem__(self, name: str) -> TpyType:
        return self.scope[name].declared

    def __iter__(self) -> Iterator[str]:
        return iter(self.scope)

    def __len__(self) -> int:
        return len(self.scope)


class _Facts(Mapping[str, object]):
    """Each name in scope declared with a THIR fact of `kind`: that fact."""

    def __init__(self, scope: Mapping[str, _Bound], kind: type) -> None:
        self.scope = scope
        self.kind = kind

    def __getitem__(self, name: str) -> object:
        fact = self.scope[name].fact
        if not isinstance(fact, self.kind):
            raise KeyError(name)
        return fact

    def __iter__(self) -> Iterator[str]:
        return (name for name in self.scope if name in self)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _HeldRecords(Mapping[str, th.THIRBorrowedRecord]):
    """Each name in scope whose binding holds a record: that record and the
    holder's access."""

    def __init__(self, scope: Mapping[str, _Bound]) -> None:
        self.scope = scope

    def __getitem__(self, name: str) -> th.THIRBorrowedRecord:
        shape = self.scope[name].plan.shape
        if not (shape.is_pointer and shape.cls is MIRClass.RECORD):
            raise KeyError(name)
        return th.THIRBorrowedRecord(shape.type, shape.readonly)

    def __iter__(self) -> Iterator[str]:
        return (name for name in self.scope if name in self)

    def __len__(self) -> int:
        return sum(1 for _ in self)


def _family_view(typ: TpyType) -> TpyType | None:
    """The view an owned leaf's family lends it through; None outside a view family."""
    family = view_family_of(typ)
    return family.view_type if family is not None else None


def _global_place(expr: th.THIRName | th.THIRModuleVar | th.THIRWalrus) -> PGlobalPlace:
    fact = expr.global_binding
    return PGlobalPlace(MIRGlobalId(fact.module, fact.name))


# The literal-into-slot coercions that render the inner literal directly in
# the slot's type; any other coercion converts a value.
_LITERAL_COERCIONS = frozenset({"int_literal_to_fixed_int", "float_literal_to_float", "float_literal_to_float32"})
# The print forms that stream a scalar leaf through the runtime's formatter.
_SCALAR_PRINT_FORMS = frozenset({th.PrintForm.RAW, th.PrintForm.INT8, th.PrintForm.BOOL,
                                 th.PrintForm.FLOAT, th.PrintForm.FLOAT32})
# The print forms that stream an owned leaf through the runtime: its own
# `operator<<`, or the bytes printer.
_OWNED_PRINT_FORMS = frozenset({th.PrintForm.RAW, th.PrintForm.BYTES})


class _Coverage:
    """MIR's check pass: walks a THIR body once, refusing what MIR does not
    model, and returns the body's lowering plan (`plan.py`)."""

    def __init__(self, fn: th.THIRFunction, definitions: MIRDefinitions,
                 summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None,
                 initialization: MIRConstructorDefinition | None = None,
                 constructor: th.THIRConstructor | None = None) -> None:
        self.fn = fn
        self.initialization = initialization
        self.constructor = constructor
        self.parameters = {p.name for p in fn.params}
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()
        self.definitions = definitions
        self.records: dict[NominalType, MIRConstructorDefinition | MIROwnedLeafDefinition | MIRHeldLayout] = {}
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
        self.range_counters: set[str] = set()
        # The mutable-access members of every container type the body reaches.
        self.members: dict[NominalType, tuple[MIRShape, ...]] = {}
        # A container result: owned storage moved out, or a borrow of a container place.
        self.container_result: th.THIRBorrowedRecord | None = None
        self.owned_container_result: NominalType | None = None
        # An `Own[R]` record result: the body's own record storage moved out.
        self.owned_record_result: NominalType | None = None
        self.iteration_references: set[str] = set()
        # Expressions whose storage the current full expression owns: record
        # constructor temporaries and owned-leaf temporaries.
        self.active_temporaries: list[th.THIRExpr] | None = None
        self.summaries = summaries if summaries is not None else {}
        self.calls: IdentityMap[th.THIRCall | th.THIRMethodCall, MIRCallSummary] = IdentityMap()
        # Each user call's borrowed result, at the access its receiver is bound at.
        self.call_results: IdentityMap[th.THIRCall | th.THIRMethodCall, th.THIRBorrowedRecord | None] = IdentityMap()
        self.stub_summaries: dict[th.THIRStubIdentity, MIRCallSummary] = {}
        self.argument_temporaries: IdentityMap[th.THIRArgTemp, PTemporary] = IdentityMap()
        self.select_temporaries: IdentityMap[th.THIRSlotEmplace, PTemporary] = IdentityMap()
        # The leaf type each checked expression has in MIR; a number literal's
        # comes from its context, not its node.
        self.types: IdentityMap[th.THIRExpr, TpyType] = IdentityMap()
        # The binding each name in scope denotes, decided once where it is declared.
        self.scope: dict[str, _Bound] = {}
        if initialization is not None:
            # The receiver under construction has the body-side layout, whose
            # definition a caller may not construct through.
            self.records[initialization.layout.type] = initialization

    def shape(self, typ: TpyType, reference: th.THIRBorrowedRecord | None = None, **facts: object) -> MIRShape:
        """The shape a slot holding a value of `typ` under these facts has (`_slot_shape`)."""
        return _slot_shape(typ, self.members, reference, **facts)

    def bind(self, name: str, declared: TpyType, shape: MIRShape, backing: MIRShape | None = None,
             fact: _BindingFact | None = None) -> PBinding:
        plan = PBinding(name, shape, backing)
        self.scope[name] = _Bound(plan, declared, fact)
        return plan

    @property
    def optionals(self) -> Mapping[str, th.THIROptionalLayout]:
        return _Facts(self.scope, th.THIROptionalLayout)

    @property
    def unions(self) -> Mapping[str, th.THIRUnionLayout]:
        return _Facts(self.scope, th.THIRUnionLayout)

    @property
    def tuples(self) -> Mapping[str, th.THIRTupleLayout]:
        return _Facts(self.scope, th.THIRTupleLayout)

    @property
    def containers(self) -> Mapping[str, th.THIRNativeContainer]:
        """The borrowed containers: a container parameter, a container alias."""
        return _Facts(self.scope, th.THIRNativeContainer)

    @property
    def optional_record_storage(self) -> Mapping[str, th.THIRBorrowedRecord]:
        """The hoisted Optional record backings: the record each holds."""
        return _Facts(self.scope, th.THIRBorrowedRecord)

    @property
    def bindings(self) -> Mapping[str, TpyType]:
        """Each name's declared type, which its reads are checked against."""
        return _DeclaredTypes(self.scope)

    @property
    def references(self) -> Mapping[str, th.THIRBorrowedRecord]:
        """Each name holding a record: the record and the holder's access."""
        return _HeldRecords(self.scope)

    def bound(self, name: str) -> MIRShape | None:
        bound = self.scope.get(name)
        return bound.plan.shape if bound is not None else None

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

    def call(self, expr: th.THIRCall | th.THIRMethodCall, *, temporary: bool = False) -> PCall:
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
        result = borrowed_result_of(callee.signature)
        owned = owned_value_type(callee.signature.return_type)
        if result is not None and view_leaf(result.type):
            pass
        elif result is not None and native_container_type(result.type):
            # A container returned by reference: a place of the members its type derives.
            self.container_members(expr, result.type)
        elif result is not None:
            self.reference(expr, result, callee.signature.return_type)
            self.records[result.type] = self.definitions.get(expr, result.type)
        elif owned is not None:
            self.leaf_layout(expr, owned)
        arguments = _call_arguments(expr)
        # The paths of the loans the callee may store: under a lent record
        # argument as a write path is, under the result's or a handed-over
        # argument's own record storage from its type.
        transferred: list[tuple[int, tuple[object, ...]]] = []
        for t in summary.transfers:
            for end in transfer_ends(t, summary.parameters, callee.signature.return_type):
                _require(expr, end is not None, "invalid loan transfer")
                if end.parameter is not None:
                    transferred.append((end.parameter, end.path))
                    continue
                _require(expr, end.owned_hops is not None and all(
                    MIRField(MIRFieldId(f.owner, f.name), f.type) in self.record_layout(expr, storage).fields
                    for storage, f in end.owned_hops), "call write field does not match record layout")
        for parameter, path in (*((w.parameter, w.path) for w in summary.writes),
                                *((o.parameter, o.path) for o in summary.returns), *transferred):
            # Each field of a path is a member of the record storage it is
            # read from: hop 0 the storage the argument binds (a descendant
            # of the parameter's record carries its fields), hop k the
            # previous field's member record. A container projection ends it.
            storage = self.bound_record(arguments[parameter]) if parameter < len(arguments) else None
            hops = path_hops(summary.parameters[parameter], path, storage)
            _require(expr, hops is not None, "call write field does not match record layout")
            for record, field in hops:
                _require(expr, MIRField(MIRFieldId(field.owner, field.name), field.type)
                         in self.record_layout(expr, record).fields,
                         "call write field does not match record layout")
        # An `Own[T]` return hands the caller a plain T.
        record = owned_record_result(callee.signature.return_type) if result is None else None
        _require(expr, (unwrap_readonly(unwrap_ref_type(expr.result_type)) == result.type if result is not None
                       else expr.result_type == owned if owned is not None
                       else expr.result_type == record if record is not None
                       else expr.result_type == callee.signature.return_type)
                 and len(_call_arguments(expr)) == len(summary.parameters), "call signature mismatch")
        # The access parameter 0 is bound at: a follows-receiver result takes it.
        receiver_readonly = False
        plans: list[object] = []
        for index, (arg, binding) in enumerate(zip(_call_arguments(expr), summary.parameters)):
            ref = binding.borrowed_record
            if ref is None or binding.optional_layout is not None:
                plans.append(self.argument(arg, binding))
            else:
                if isinstance(arg, th.THIRArgTemp):
                    # A temporary receiver binds readonly (argument_temporary
                    # requires it), so its follows-receiver result is readonly
                    # too: narrower than the mutable clone C++ may pick, never wider.
                    plans.append(self.argument_temporary(arg, ref))
                    receiver_readonly = receiver_readonly or index == 0
                    continue
                if isinstance(arg, th.THIRFieldAccess):
                    # A member place, lent for the call through a holder of it.
                    source = self.record_source(arg)
                else:
                    _require(arg, isinstance(arg, (th.THIRName, th.THIRSelf))
                             and arg.form is th.Form.BORROW, "call needs borrowed record name")
                    source_name = arg.name if isinstance(arg, th.THIRName) else "self"
                    _require(arg, not self.payload_alias(source_name), "call needs unwrapped record binding")
                    source = self.record_source(arg)
                plans.append(source)
                actual = source.record
                _require(arg, self.binds_at(arg, actual.type, ref.type) and (not actual.readonly or ref.readonly),
                         "call record argument mismatch")
                self.lent_loans(arg, actual.type, ref.type)
                # The binding's access must be the one C++ picks the overload
                # by; a follows-receiver result is bound at it.
                _require(arg, index != 0 or not isinstance(expr, th.THIRMethodCall)
                         or expr.receiver_access == th.THIRBorrowedRecord(actual.type, actual.readonly),
                         "call receiver access disagrees with its binding")
                receiver_readonly = receiver_readonly or index == 0 and actual.readonly
        self.argument_order_rule(expr, _call_arguments(expr))
        self.calls[expr] = summary
        self.call_results[expr] = summary.result_at(receiver_readonly)
        return PCall(expr.loc, summary, tuple(plans), isinstance(expr, th.THIRMethodCall))

    def stub_call(self, expr: th.THIRCall) -> PCall:
        """A call to a stub, summarized from its declaration alone
        (`call_contract.stub_summary`); its arguments follow the user-call
        rows, and a protocol parameter admits only a builtin leaf argument,
        whose dispatch runs the stub's own runtime code."""
        _plain(expr, {"callee", "args", "native_name", "cpp_template", "callee_cpp", "stub_callee", "constructs"}
               | _value_result_form(expr))
        callee = expr.stub_callee
        _require(expr, expr.resolved_callee is None, "call has both a resolved and a stub callee")
        # Calls of one stub share one summary object, as the body's summary table holds it.
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
        plans: list[object] = []
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
                    plans.append(self.container_place(arg))
                    self.writes[arg] = False
                    continue
                if owned_leaf(read):
                    plans.append(self.lent_value(arg))
                    continue
                # An owned-leaf or view result may borrow what the parameter
                # binds; a scalar argument has no storage to borrow.
                _require(arg, owned is None and view_result(signature.return_type) is None,
                         "stub result may borrow a scalar argument")
            plans.append(self.argument(arg, binding))
        self.argument_order_rule(expr, expr.args)
        self.calls[expr] = summary
        return PCall(expr.loc, summary, tuple(plans), False)

    def method_call(self, expr: th.THIRMethodCall, *, view: bool = False) -> PCall:
        """A call to a native container's method stub, summarized from its
        declaration (`call_contract.stub_summary`): the receiver place is
        parameter 0, lent whole; the arguments follow the stub-call rows.
        With `view`, the result is a container view of the receiver's
        region."""
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
        # region; a container view's is the view holder (`view_call`).
        result = summary.borrowed_result
        _require(expr, result is None if not view else result is not None and container_view(result.type),
                 "borrowed method result")
        # An `Own[T]` result hands the caller a plain T.
        _require(expr, (expr.result_type == owned if owned is not None
                        else expr.result_type == unwrap_own(signature.return_type))
                 and len(expr.args) + 1 == len(summary.parameters), "call signature mismatch")
        place = self.container_place(expr.receiver)
        plans: list[object] = [place]
        receiver = summary.parameters[0]
        # A pure method reads its receiver whatever its declared access (the
        # mutable clone of an `@auto_readonly` accessor).
        _require(expr, receiver.type == place.type and (receiver.readonly or not place.readonly
                                                         or callee.contract is th.THIRStubContract.PURE),
                 "call container argument mismatch")
        for arg, binding in zip(expr.args, summary.parameters[1:]):
            if binding.protocol:
                read = through_view(arg.result_type)
                _require(arg, stub_protocol_argument(read) and not self.container_receiver(arg),
                         "stub protocol argument is not a builtin leaf")
                if owned_leaf(read):
                    plans.append(self.lent_value(arg))
                    continue
            plans.append(self.argument(arg, binding))
        self.argument_order_rule(expr, expr.args)
        self.calls[expr] = summary
        return PCall(expr.loc, summary, tuple(plans), True)

    def view_call(self, expr: th.THIRMethodCall) -> PContainer:
        """A container view a method stub returns (`d.keys()`): a holder of
        its receiver's elements region, readonly when the view's elements
        or the receiver are."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        call = self.method_call(expr, view=True)
        result = call.summary.borrowed_result
        members = self.container_members(expr, result.type)
        readonly = result.readonly or call.arguments[0].readonly
        self.writes[expr] = False
        return PContainer(result.type, with_access(members, readonly), readonly, PContainerCall(expr.loc, call),
                          self.container_view_shape(result.type, readonly))

    def argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> object:
        """One argument at its binding: an owned leaf lent or copied, else
        any admitted scalar expression, evaluated into a temporary before
        the call (a scalar global is read there). Evaluation order among
        arguments is the order rule's (`argument_order_rule`)."""
        if binding.optional_layout is not None:
            return self.optional_operand(arg, binding.type, binding.optional_layout)
        if owned_value_type(binding.type) is not None:
            return self.owned_argument(arg, binding.type, binding.passing)
        if view_leaf(binding.type):
            # A view passed by value hands the callee the loan it holds.
            return self.view_value(arg, binding.type)
        if record_type(binding.type) and binding.borrowed_record is None and binding.passing in OWNING_PASSINGS:
            # A record handed over by value (an `Own[R]` parameter, an
            # element a container takes): built, or handed over by a call,
            # into a temporary of the full expression the call moves from,
            # whose stored loans its construct or the callee's transfers name.
            # A named, copied or moved record stays refused: the summary
            # treats an `Own[R]` parameter's object as private to the callee
            # (what it stores there is no transfer), which only a temporary
            # no other argument or holder of the caller's reaches makes true.
            _require(arg, isinstance(arg, th.THIRCtorCall) or _user_call(arg), "unsupported record argument")
            return self.literal_record(arg, binding.type)
        if native_container_type(binding.type) or container_view(binding.type):
            return self.container_argument(arg, binding)
        typ = arg.result_type if binding.protocol else binding.type
        plan = self.expr(arg, typ)
        _require(arg, plan.type == typ, "call scalar argument mismatch")
        return plan

    def container_argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> PContainerMove | PContainer:
        """A container argument: a place lent whole for the call (a Span
        holder lends the region it views), or owned container storage moved
        into the temporary the callee takes over."""
        if binding.passing in OWNING_PASSINGS:
            _require(arg, isinstance(arg, th.THIRMove) and isinstance(arg.value, th.THIRName)
                     and arg.value.global_binding is None and self.owned_container_bound(arg.value.name),
                     "container argument needs an owned move")
            _plain(arg, {"value"})
            _plain(arg.value, {"name", "is_last_use", "is_movable"})
            _require(arg, self.bindings[arg.value.name] == binding.type, "call container argument mismatch")
            _require(arg, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append(arg)
            plan: PContainerMove | PContainer = PContainerMove(
                arg.loc, self.shape(binding.type, storage=True, members=self.members[binding.type]), arg.value.name)
        else:
            _require(arg, self.container_receiver(arg), "call container argument needs a place")
            plan = self.container_place(arg)
            _require(arg, plan.type == binding.type and (binding.readonly or not plan.readonly),
                     "call container argument mismatch")
        self.writes[arg] = False
        return plan

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

    def owned_argument(self, arg: th.THIRExpr, typ: TpyType, passing: ParamPassing) -> POwnedArgument:
        """An owned-leaf argument: lent for the call at a borrowing passing
        (a name or literal in place, anything built a temporary of the full
        expression), else the callee's own copy, copied or built into a
        temporary the full expression owns."""
        if passing in BORROWING_PASSINGS:
            if view_leaf(arg.result_type):
                # A view of the parameter's family is lent as the view it is.
                plan = self.view_value(arg, arg.result_type)
                _require(arg, view_compatible(arg.result_type, typ), "call owned-leaf argument mismatch")
                return POwnedArgument(arg.loc, typ, True, plan)
            plan = self.owned_value(arg, sink=False)
            _require(arg, plan.type == typ, "call owned-leaf argument mismatch")
            return POwnedArgument(arg.loc, typ, True, plan)
        _require(arg, passing in OWNING_PASSINGS, "unsupported owned-leaf argument passing")
        plan = self.owned_value(arg, sink=True)
        _require(arg, plan.type == typ, "call owned-leaf argument mismatch")
        _require(arg, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
        self.active_temporaries.append(arg)
        return POwnedArgument(arg.loc, typ, False, plan)

    def argument_temporary(self, arg: th.THIRArgTemp, reference: th.THIRBorrowedRecord, *,
                           addr_of: bool = False) -> PTemporary:
        """A record built for a call into storage the temporary plan places,
        lent through a holder of it; `addr_of`: its address is what the
        argument passes (an Optional record parameter's pointer)."""
        _require(arg, self.fn.temp_plan is not None, "named argument needs complete temporary plan")
        placement = self.fn.temp_plan.placement(arg)
        _plain(arg, {"init", "cpp_type", "movable"} | ({"addr_of"} if addr_of else set()))
        _require(arg, arg.addr_of is addr_of, "named argument address mismatch")
        _require(arg, arg.form is th.Form.BORROW and (isinstance(arg.init, th.THIRCtorCall) or _user_call(arg.init))
                 and arg.result_type == reference.type and reference.readonly,
                 "named argument needs readonly record constructor")
        value = self.record_value(arg.init, reference.type, call=True)
        self.stable_constructor_operands(arg.init)
        definition = self.records[reference.type]
        _require(arg, not placement.optional or definition.layout.movable and arg.would_bank(),
                 "deferred argument needs movable backing")
        temporary = self.placed(arg, placement, reference, value)
        self.argument_temporaries[arg] = temporary
        return temporary

    def placed(self, node: th.THIRArgTemp | th.THIRSlotEmplace, placement: object, reference: th.THIRBorrowedRecord,
               value: object) -> PTemporary:
        """A record in storage the temporary plan places, lent through a holder of it."""
        return PTemporary(node, node.loc, placement.index, placement.scope, placement.optional,
                          self.shape(reference.type, storage=True), self.shape(reference.type, reference), value)

    def anchored(self, node: object, plan: object) -> object:
        """`plan`, with the temporaries the temporary plan initializes at its node built first."""
        if isinstance(plan, PAnchored) and isinstance(plan.body, PTemporary) and plan.body.key is node:
            # An emplacement that is also the arm it fills is anchored once.
            return plan
        found = self.fn.temp_plan.initializations.get(node, ()) if self.fn.temp_plan is not None else ()
        temporaries = tuple(t for t in map(self.temporary_of, (p.node for p in found)) if t is not None)
        return PAnchored(temporaries, plan) if temporaries else plan

    def temporary_of(self, node: object) -> PTemporary | None:
        return self.argument_temporaries.get(node) or self.select_temporaries.get(node)

    def select_temporary(self, expr: th.THIRSlotEmplace, reference: th.THIRBorrowedRecord) -> PTemporary:
        _require(expr, self.fn.temp_plan is not None, "select storage needs complete temporary plan")
        placement = self.fn.temp_plan.placement(expr)
        _plain(expr, {"value", "cpp_type"})
        _require(expr, placement.optional and placement.initialization is expr,
                 "select storage needs conditional emplacement")
        _require(expr, isinstance(expr.value, th.THIRCtorCall), "select storage needs record constructor")
        value = self.record_value(expr.value, reference.type)
        self.stable_constructor_operands(expr.value)
        _require(expr, self.records[reference.type].layout.movable, "select storage needs movable backing")
        temporary = self.placed(expr, placement, reference, value)
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
                     and operand.global_binding is None and operand.name not in self.optionals
                     and operand.name not in self.unions, "named temporary needs stable scalar operands")

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
                return (expr.global_binding is None and expr.name not in self.optionals
                        and expr.name not in self.unions and not self.payload_alias(expr.name))
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
        if previous is None or fact.writable:
            self.globals[identity] = fact
        return fact.type

    def check(self) -> PBody:
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
        result = (bound_result(signature, receiver_readonly)
                  if signature is not None and signature.borrowed_result is not None else None)
        if result is not None:
            self.reference(fn, result, fn.return_type)
            self.records[result.type] = self.definitions.get(fn, result.type)
            self.result = result
        elif (owned := owned_value_type(fn.return_type)) is not None:
            _require(fn, return_representation(fn.return_type) is Representation.STORAGE,
                     "unsupported return type")
            self.leaf_layout(fn, owned)
        elif view_leaf(fn.return_type):
            self.result = view_result(fn.return_type)
            _require(fn, self.result is not None, "unsupported return type")
        elif _region_view(fn.return_type):
            # A container view result views a region the caller reaches through the
            # arguments: a borrowed result, summarized by its origins.
            self.container_members(fn, unwrap_readonly(unwrap_ref_type(fn.return_type)))
            self.result = view_result(fn.return_type)
            _require(fn, self.result is not None, "unsupported return type")
        elif native_container_type(bare := unwrap_readonly(unwrap_ref_type(fn.return_type))):
            # A container returned by reference is borrowed like a record result.
            self.result = self.container_result = (
                bound_result(signature, receiver_readonly) if signature is not None
                and signature.result_follows_receiver else container_result(fn.return_type))
            _require(fn, self.result is not None, "unsupported return type")
            self.container_members(fn, bare)
        elif native_container_type(bare := unwrap_own(fn.return_type)):
            # An `Own[...]` container returns the body's storage by value.
            _require(fn, return_representation(fn.return_type) is Representation.STORAGE, "unsupported return type")
            self.container_members(fn, bare)
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
        receiver = None
        if fn.receiver is not None:
            self.reference(fn, fn.receiver, fn.receiver.type)
            self.parameters.add("self")
            binding = PBinding("self", self.shape(fn.receiver.type, fn.receiver))
            self.scope["self"] = _Bound(binding, fn.receiver.type)
            # A constructor's receiver is the object it builds, passed by no caller.
            receiver = PParameter(binding, th.receiver_param(fn.receiver).passing
                                  if self.initialization is None else None, None)
        parameters: list[PParameter] = []
        for p in fn.params:
            _plain(p, {"name", "type", "passing", "borrowed_record", "optional_layout", "union_layout", "tuple_layout",
                        "native_container"})
            _require(fn, p.name not in self.bindings, "duplicate binding")
            _require(p, isinstance(p.passing, ParamPassing), "unpublished parameter passing")
            if _region_view(p.type):
                # A container view passed by value is the caller's loan of a region, held by the parameter.
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout,
                                                    p.tuple_layout)), "conflicting parameter facts")
                _require(p, p.passing is ParamPassing.VALUE, "unsupported view parameter passing")
                bare = unwrap_readonly(unwrap_ref_type(p.type))
                _require(p, p.native_container is None or p.native_container.type == bare,
                         "invalid native container fact")
                self.container_members(p, bare)
                fact = _container_view_fact(bare, False)
                self.bind(p.name, bare,
                          self.shape(bare, fact, members=with_access(self.members[bare], fact.readonly)))
            elif p.native_container is not None:
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout, p.tuple_layout)),
                         "conflicting parameter facts")
                self.container(p, p.native_container, p.type)
                if p.passing is ParamPassing.OWN:
                    # An `Own[...]` container is the body's own storage from entry.
                    _require(p, not p.native_container.readonly, "readonly owned container parameter")
                    typ = p.native_container.type
                    self.bind(p.name, typ, self.shape(typ, storage=True, members=self.members[typ]))
                else:
                    _require(p, p.passing in (ParamPassing.CONST_REF, ParamPassing.MUT_REF),
                             "unsupported container parameter passing")
                    self.bind(p.name, p.native_container.type, self.shape(p.type, container=p.native_container),
                              fact=p.native_container)
            elif p.tuple_layout is not None:
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout)),
                         "conflicting parameter facts")
                self.tuple_layout(p, p.tuple_layout, p.type)
                self.bind(p.name, unwrap_ref_type(unwrap_readonly(unwrap_ref_type(p.type))),
                          self.shape(p.type, tuple_layout=p.tuple_layout), fact=p.tuple_layout)
            elif p.union_layout is not None:
                _require(p, p.borrowed_record is None and p.optional_layout is None, "conflicting parameter facts")
                self.union_layout(p, p.union_layout, p.type)
                self.bind(p.name, p.union_layout.type, self.shape(p.type, union_layout=p.union_layout),
                          fact=p.union_layout)
            elif p.optional_layout is not None:
                _require(p, p.borrowed_record is None, "conflicting parameter facts")
                self.optional_layout(p, p.optional_layout, p.type)
                self.bind(p.name, unwrap_readonly(unwrap_ref_type(p.type)),
                          self.shape(p.type, optional_layout=p.optional_layout), fact=p.optional_layout)
            elif p.borrowed_record is not None:
                self.reference(p, p.borrowed_record, p.type)
                self.bind(p.name, p.borrowed_record.type, self.shape(p.type, p.borrowed_record))
            elif (owned := owned_value_type(p.type)) is not None:
                _require(p, p.passing in BORROWING_PASSINGS | OWNING_PASSINGS,
                         "unsupported owned-leaf parameter passing")
                self.leaf_layout(p, owned)
                # A borrowed one is a readonly holder of the caller's storage.
                self.bind(p.name, owned, self.shape(owned, th.THIRBorrowedRecord(owned, True))
                          if p.passing in BORROWING_PASSINGS else self.shape(owned, storage=True))
            elif view_leaf(p.type):
                # A view passed by value is the caller's loan, held by the parameter.
                _require(p, p.passing is ParamPassing.VALUE, "unsupported view parameter passing")
                self.bind(p.name, p.type, self.shape(p.type, _view_holder_fact(p.type)))
            elif (record := owned_record_parameter(p)) is not None:
                # A record handed over at OWN binds the caller's temporary
                # (`R&&`), which ends with the caller's full expression. It
                # is modeled as the body's own storage ending at body exit:
                # an earlier end, so never a missed conflict. Reached through
                # a holder as an owned record local is; never reseated. The
                # loans it stores are the caller's, seeded at entry.
                self.record_layout(p, record)
                self.owned_records.add(p.name)
                self.fixed_owned.add(p.name)
                self.bind(p.name, record, self.shape(record, th.THIRBorrowedRecord(record, False)),
                          self.shape(record, storage=True))
            else:
                _require(p, not container_view(unwrap_readonly(unwrap_ref_type(p.type))), "view of container storage")
                self.unpublished_container(p, p.type)
                _require(fn, scalar_param(p), "unsupported parameter type")
                self.bind(p.name, p.type, self.shape(p.type))
            binding = self.scope[p.name].plan
            shape = binding.shape
            # Owned storage lives for the body; a scalar union's payload is the caller's.
            parameters.append(PParameter(binding, p.passing, MIRStorageDuration.BODY
                                         if binding.backing is not None or shape.is_storage
                                         else MIRStorageDuration.CALLER
                                         if shape.cls is MIRClass.UNION and shape.scalar_wrapper else None))
        statements = self.declarations(fn.body, 0)
        planned = bool(self.argument_temporaries or self.select_temporaries)
        if planned:
            _require(fn, len(self.argument_temporaries) + len(self.select_temporaries) == len(fn.temp_plan.placements),
                     "temporary plan contains unsupported record storage")
        # Each callee once, as the body's summary table holds them.
        callees = tuple({s.callee.identity: s for s in self.calls.values()}.values())
        globals_: list[tuple[MIRGlobalId, PBinding]] = []
        for identity, fact in self.globals.items():
            # A global is writable when any use writes it; an owned-leaf
            # global is read through a readonly handle on its external storage.
            reference = th.THIRBorrowedRecord(fact.type, True) if owned_leaf(fact.type) else None
            globals_.append((identity, PBinding(fact.name, self.shape(fact.type, reference,
                                                                      readonly=not fact.writable))))
        if self.initialization is not None:
            # The receiver's entry initialization builds every container
            # member, a composed member's included.
            pending = list(self.initialization.initializers)
            while pending:
                init = pending.pop()
                if isinstance(init.source, MIRComposedConstruct):
                    pending.extend(init.source.initializers)
                elif native_container_type(init.field.type):
                    self.container_members(self.constructor, init.field.type)
        self.close_layouts()
        return PBody(MIRBodyKind.CONSTRUCTOR if self.initialization is not None else function_body_kind(fn),
                     fn.return_type, isinstance(fn.return_type, VoidType), receiver, tuple(parameters),
                     tuple(globals_), statements, planned, callees, self.result,
                     self.initialization.initializers if self.initialization is not None else None,
                     MappingProxyType({t: d.layout for t, d in self.records.items()}))

    def declarations(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> tuple[object, ...]:
        """The plans of a statement list: each statement's, after the
        temporaries it declares and initializes (an if or a while carries
        its own)."""
        plans: list[object] = []
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
        return tuple(plans)

    def statement_temporaries(self, stmt: th.THIRStmt) -> PStatementTemporaries | None:
        """The temporaries the temporary plan declares at a statement (each
        in its scope), and those it initializes there."""
        plan = self.fn.temp_plan
        if plan is None or (stmt not in plan.declarations and stmt not in plan.initializations):
            return None
        return PStatementTemporaries(tuple(self.temporary_of(p.node) for p in plan.declarations.get(stmt, ())),
                                     tuple(self.temporary_of(p.node) for p in plan.initializations.get(stmt, ())))

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

    def declare(self, loc: object, binding: PBinding, value: PFullExpression | None) -> PDeclare:
        """A local's declaration: storage the binding owns is the scope's."""
        return PDeclare(loc, binding, value, False if binding.shape.is_storage else None)

    def missing_placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, needs: bool) -> th.THIRStmt | None:
        """The declaration, when its storage needs a placement THIR did not
        give: the emit pass refuses it where the CFG reaches it, which only
        the emit pass knows."""
        return stmt if needs and self.placement(stmt) is None else None

    def declaration(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl, loops: int) -> object:
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
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
            _require(stmt, stmt.form is th.Form.BORROW and stmt.is_const == fact.readonly
                     and place.type == fact.type and (fact.readonly or not place.readonly)
                     and (not isinstance(stmt.init, th.THIRName) or stmt.init.name in self.containers)
                     and stmt.name not in self.fn.layout.reassigned_locals,
                     "container alias needs fixed matching borrowed source")
            binding = self.bind(stmt.name, fact.type, self.shape(stmt.resolved_type, container=fact), fact=fact)
            return PContainerAlias(stmt.loc, binding, place, stmt)
        if stmt.union_layout is not None:
            _require(stmt, isinstance(stmt, th.THIRVarDecl) and stmt.init is not None,
                     "unsupported union declaration")
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "union_layout",
                          "cpp_local_representation", "union_literal", "storage_placement"})
            self.union_layout(stmt, stmt.union_layout, stmt.resolved_type)
            reference = any(isinstance(m, th.THIRBorrowedRecord) for m in stmt.union_layout.elements)
            _require(stmt, stmt.form is (th.Form.BORROW if reference else th.Form.VALUE)
                     and (reference or stmt.cpp_local_representation is None),
                     "union declaration representation mismatch")
            value = self.union_source(stmt.init, stmt.union_layout, stmt.union_literal)
            binding = self.bind(stmt.name, stmt.union_layout.type,
                                self.shape(stmt.union_layout.type, union_layout=stmt.union_layout),
                                fact=stmt.union_layout)
            # A scalar payload lives in storage of the declaration's scope.
            scalar = all(m is None or storage_leaf(m) for m in stmt.union_layout.elements)
            return PUnionDeclare(stmt.loc, binding, value, self.placement(stmt) if scalar else None,
                                 self.missing_placement(stmt, scalar))
        if stmt.optional_layout is not None:
            return self.optional_declaration(stmt)
        if isinstance(stmt, th.THIRVarDecl) and stmt.tuple_storage_alias is not None:
            return self.tuple_alias(stmt)
        if isinstance(stmt, th.THIRVarDecl) and isinstance(stmt.resolved_type, TupleType):
            owning = isinstance(stmt.tuple_layout, th.THIRTupleLayout) and stmt.tuple_layout.owns_records
            _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "tuple_layout"}
                   | ({"storage_placement"} if owning else set()))
            _require(stmt, stmt.form in ((th.Form.STORAGE,) if owning else (th.Form.VALUE, th.Form.BORROW))
                     and stmt.init is not None, "unsupported tuple declaration")
            self.tuple_layout(stmt, stmt.tuple_layout, stmt.resolved_type, allow_owned=owning)
            if owning:
                _require(stmt, isinstance(stmt.init, th.THIRTupleLiteral)
                         and stmt.init.tuple_layout == stmt.tuple_layout
                         and stmt.storage_placement is th.THIRStoragePlacement.SCOPE,
                         "owned tuple needs direct constructor literal")
            value = self.tuple_expr(stmt.init, allow_owned=owning)
            self.tuple_compatible(stmt, value.layout, stmt.tuple_layout)
            binding = self.bind(stmt.name, stmt.resolved_type,
                                self.shape(stmt.resolved_type, tuple_layout=stmt.tuple_layout), fact=stmt.tuple_layout)
            if owning and id(stmt) in self.body_declarations:
                self.tuple_roots[stmt.name] = stmt.name
            if not owning:
                return self.declare(stmt.loc, binding, PFullExpression(value.loc, value, False))
            return POwnedTupleDeclare(stmt.loc, binding, value)
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
        if _region_view(stmt.resolved_type):
            return self.span_local(stmt)
        _require(stmt, not container_view(unwrap_readonly(unwrap_ref_type(stmt.resolved_type))),
                 "view of container storage")
        self.unpublished_container(stmt, stmt.resolved_type)
        _require(stmt, not holds_borrowing_view(stmt.resolved_type), "view local")
        _require(stmt, stmt.form is th.Form.VALUE and storage_leaf(stmt.resolved_type),
                 "unsupported local type or form")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        value = None
        if stmt.init is not None:
            value = self.full_expression(stmt.init, expected=stmt.resolved_type)
            _require(stmt, value.type == stmt.resolved_type, "initializer type mismatch")
        return self.declare(stmt.loc, self.bind(stmt.name, stmt.resolved_type, self.shape(stmt.resolved_type)), value)

    def optional_declaration(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> POptionalDeclare:
        """An Optional local: from its source, or (`OPT_RVALUE`) a record
        built into its own backing; a scalar payload lives in storage of the
        declaration's scope."""
        allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "optional_layout", "storage_placement"}
        backing = value = None
        if isinstance(stmt, th.THIRVarDecl):
            reference = isinstance(stmt.optional_layout.payload, th.THIRBorrowedRecord)
            if reference:
                allowed |= {"cpp_local_representation"}
            _require(stmt, stmt.form is (th.Form.BORROW if reference else th.Form.VALUE)
                     and stmt.init is not None, "optional declaration form or initializer")
            _require(stmt, not reference or stmt.is_const == stmt.optional_layout.payload.readonly,
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
                backing = PRecordBacking(stmt.init.loc, self.shape(fact.type, storage=True), self.shape(fact.type, fact))
            else:
                _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE and stmt.init is None,
                         "optional backing storage")
        _plain(stmt, allowed)
        self.optional_layout(stmt, stmt.optional_layout, stmt.resolved_type)
        if stmt.owned_storage is None:
            value = self.optional_source(stmt.init, stmt.optional_layout)
        binding = self.bind(stmt.name, unwrap_readonly(unwrap_ref_type(stmt.resolved_type)),
                            self.shape(stmt.resolved_type, optional_layout=stmt.optional_layout),
                            fact=stmt.optional_layout)
        scalar = storage_leaf(stmt.optional_layout.payload)
        # A holder pointing into a field's payload: a loan replacing the field ends.
        field = isinstance(stmt.init, th.THIRFormConvert) and isinstance(stmt.init.value, th.THIRFieldAccess)
        return POptionalDeclare(stmt.loc, binding, value, self.placement(stmt) if scalar else None, backing,
                                stmt if field else None, self.missing_placement(stmt, scalar or backing is not None))

    def owned_local(self, stmt: th.THIRVarDecl) -> PDeclare:
        typ = stmt.resolved_type
        _require(stmt, stmt.form in (th.Form.VALUE, th.Form.STORAGE), "unsupported local type or form")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        # A declaration without a value would default-construct storage no write event names.
        _require(stmt, stmt.init is not None, "owned-leaf declaration needs an initializer")
        value = self.full_expression(stmt.init, check=lambda: self.owned_value(stmt.init, sink=True))
        _require(stmt, value.type == typ, "initializer type mismatch")
        self.leaf_layout(stmt, typ)
        return self.declare(stmt.loc, self.bind(stmt.name, typ, self.shape(typ, storage=True)), value)

    def owned_container_local(self, stmt: th.THIRVarDecl) -> PDeclare:
        """A local holding its own container: built from a literal, the
        body's storage from its declaration."""
        _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation",
                      "native_container"})
        fact = stmt.native_container
        _require(stmt, stmt.form in (th.Form.VALUE, th.Form.STORAGE) and not fact.readonly and not stmt.is_const,
                 "unsupported local type or form")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        # A declaration without a value would default-construct storage no write event names.
        _require(stmt, stmt.init is not None, "container declaration needs an initializer")
        self.container(stmt, fact, stmt.resolved_type)
        value = self.full_expression(stmt.init, check=lambda: self.container_value(stmt.init, fact.type))
        return self.declare(stmt.loc, self.bind(stmt.name, fact.type,
                                                self.shape(fact.type, storage=True, members=self.members[fact.type])),
                            value)

    def container_value(self, expr: th.THIRExpr, typ: NominalType) -> PContainerLiteral:
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
        members = self.members[typ]
        # Literal construction: the one place the compiler knows a container's length.
        _require(expr, len(expr.values) == (0 if len(members) == 1 else len(expr.elements))
                 and (not is_array(typ) or len(expr.elements) == typ.type_args[1]),
                 "container initializer type mismatch")
        operands = (expr.elements if len(members) == 1
                    else tuple(e for pair in zip(expr.elements, expr.values) for e in pair))
        plans: list[object] = []
        for index, operand in enumerate(operands):
            member = members[index % len(members)]
            match member.cls:
                case MIRClass.LEAF:
                    plan = self.expr(operand, member.type)
                    _require(operand, plan.type == member.type, "container element type mismatch")
                case MIRClass.OWNED_LEAF:
                    plan = self.owned_value(operand, sink=False)
                    _require(operand, plan.type == member.type, "container element type mismatch")
                case _:
                    plan = self.literal_record(operand, member.type)
            plans.append(plan)
        self.argument_order_rule(expr, operands)
        self.writes[expr] = any(self.writes.get(o, False) for o in operands)
        return PContainerLiteral(expr.loc, typ, tuple(plans))

    def literal_record(self, expr: th.THIRExpr, typ: NominalType) -> PRecordTemporary | PRecordSource:
        """A record element of a container literal: constructed, or handed
        over by a call's owned result, into a temporary of the full
        expression, which the literal moves from, or copied through the
        holder a name is."""
        if isinstance(expr, th.THIRCtorCall) or _user_call(expr):
            _require(expr, self.active_temporaries is not None, "temporary needs full-expression boundary")
            value = self.record_value(expr, typ, call=True)
            self.active_temporaries.append(expr)
            return PRecordTemporary(expr.loc, typ, self.shape(typ, storage=True), value)
        _require(expr, isinstance(expr, th.THIRCopy), "unsupported container element")
        # A copied element is lent to the literal through a holder of its source.
        return self.record_value(expr, typ).source

    def element_binding(self, stmt: th.THIRVarDecl) -> PDeclare:
        """A local binding one record element in place (`p = ps[0]`): a
        borrow of the container's element region, never retargeted."""
        _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation"})
        _require(stmt, stmt.name not in self.fn.layout.reassigned_locals, "element binding is reassigned")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        fact = th.THIRBorrowedRecord(unwrap_readonly(unwrap_ref_type(stmt.resolved_type)), stmt.is_const)

        def check() -> PElementPlace:
            element = self.element_access(stmt.init)
            member = element.member
            _require(stmt, member.cls is MIRClass.RECORD and member.type == fact.type
                     and stmt.init.form is th.Form.BORROW and (not member.readonly or fact.readonly),
                     "element binding type or access mismatch")
            return element
        value = self.full_expression(stmt.init, check=check)
        return self.declare(stmt.loc, self.bind(stmt.name, fact.type, self.shape(fact.type, fact)), value)

    def span_local(self, stmt: th.THIRVarDecl) -> PDeclare:
        """A container view local: a holder of the region its source views, readonly
        when its element is or when that region is."""
        typ = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
        _require(stmt, stmt.form is th.Form.VALUE and stmt.init is not None, "unsupported local type or form")
        self.container_members(stmt, typ)
        value = self.full_expression(stmt.init, check=lambda: self.span_value(stmt.init, typ))
        return self.declare(stmt.loc, self.bind(stmt.name, typ, self.container_view_shape(typ, value.body.readonly)),
                            value)

    def container_view_shape(self, typ: NominalType, readonly: bool) -> MIRShape:
        """The shape of a container view holder (`_container_view_fact`)."""
        fact = _container_view_fact(typ, readonly)
        return self.shape(typ, fact, members=with_access(self.members[typ], fact.readonly))

    def span_value(self, expr: th.THIRExpr, holder: NominalType,
                   target: NominalType | None = None) -> PSpanAlias | PSpanRegion | PSpanSlice:
        """Check an expression a container view holder takes, whether the
        region it views is readonly included (`readonly` of the plan). Every
        Span-producing operation transfers referents and never copies: a Span
        of the holder's own type (`target`, the outermost holder's) is
        aliased, any other view's region borrowed, an unstepped slice
        borrows its receiver's whole element region after its bounds are
        read (Python slices clamp, so it does not raise)."""
        target = holder if target is None else target
        element = self.container_members(expr, holder)[0].type
        match expr:
            case th.THIRName() if expr.global_binding is None and self.container_view_access(expr.name) is not None:
                _plain(expr, {"name", "is_last_use", "is_movable"})
                source = self.bindings[expr.name]
                _require(expr, self.members[source][0].type == element, "view source type mismatch")
                readonly = self.container_view_access(expr.name) or readonly_elements(holder)
                alias = self.bound(expr.name).type == target
                plan: PSpanAlias | PSpanRegion | PSpanSlice = (
                    PSpanAlias(expr.name, readonly) if alias else PSpanRegion(expr.name, readonly))
                writing = False
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, _region_view(expr.result_type) and _region_view(expr.expr.result_type),
                         "unsupported view source")
                inner = self.span_value(expr.expr, unwrap_readonly(unwrap_ref_type(expr.expr.result_type)), target)
                # The coercion views its source in place: the source's plan, at this holder's access.
                plan = replace(inner, readonly=inner.readonly or readonly_elements(holder))
                writing = self.writes[expr.expr]
            case th.THIRStrSlice():
                _plain(expr, {"receiver", "cpp_template", "lower", "upper", "step", "stepped", "index"})
                _require(expr, not expr.stepped, "stepped slice")
                _require(expr, expr.step is None and expr.index is None, "unsupported view source")
                bounds = [b for b in (expr.lower, expr.upper) if b is not None]
                plans = []
                for bound in bounds:
                    plans.append(self.expr(bound))
                    _require(bound, int_traits_of(plans[-1].type) is not None,
                             "slice bound needs a fixed-width int")
                place = self.container_place(expr.receiver)
                _require(expr, not place.keyed and place.element.type == element,
                         "view source type mismatch")
                self.argument_order_rule(expr, tuple(bounds))
                plan = PSpanSlice(tuple(plans), place, place.readonly or readonly_elements(holder))
                writing = any(self.writes[b] for b in bounds)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        self.writes[expr] = writing
        return plan

    def view_local(self, stmt: th.THIRVarDecl) -> PDeclare:
        typ = stmt.resolved_type
        _require(stmt, stmt.form is th.Form.VALUE, "unsupported local type or form")
        # A declaration without a value holds an empty view no source names.
        _require(stmt, stmt.init is not None, "view declaration needs an initializer")
        value = self.full_expression(stmt.init, check=lambda: self.view_value(stmt.init, typ))
        return self.declare(stmt.loc, self.bind(stmt.name, typ, self.shape(typ, _view_holder_fact(typ))), value)

    def lent_value(self, expr: th.THIRExpr) -> object:
        """Check an operand an operation reads in place: a view, or an owned
        leaf borrowed in place or built into a temporary of the full
        expression."""
        if view_leaf(expr.result_type):
            return self.view_value(expr, expr.result_type)
        return self.owned_value(expr, sink=False)

    def view_value(self, expr: th.THIRExpr, holder: TpyType) -> object:
        """Check an expression whose value a view holder of type `holder`
        takes. Every view-producing operation transfers referents and never
        copies: a view is aliased, an owned leaf (a name, a global, a static
        literal, a temporary of the full expression) is borrowed, an
        unstepped slice borrows its whole receiver -- whose interior MIR does
        not model -- after its bounds are read, and a call's view result
        borrows what the call lends."""
        _require(expr, view_leaf(holder), "unsupported view source")
        typ = literal_type(expr) if isinstance(expr, th.THIRLiteral) else expr.result_type
        if not view_leaf(typ):
            source = self.owned_value(expr, sink=False)
            _require(expr, view_compatible(holder, source.type), "view source type mismatch")
            return PViewOf(expr.loc, holder, _family_view(source.type), source)
        _require(expr, self.types.get(expr, typ) == typ and view_compatible(holder, typ), "view source type mismatch")
        writing = False
        match expr:
            case th.THIRName() if expr.global_binding is None and self.view_bound(expr.name):
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
                plan: object = PViewName(expr.loc, typ, expr.name)
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
                plan = PViewOf(expr.loc, typ, typ, self.view_value(expr.expr, typ))
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
                    _require(bound, int_traits_of(plans[-1].type) is not None,
                             "slice bound needs a fixed-width int")
                plan = PViewSlice(expr.loc, typ, tuple(plans), self.view_value(expr.receiver, typ))
                # The bounds and the receiver evaluate in an unspecified order.
                operands = (*bounds, expr.receiver)
                for operand in operands:
                    if self.writes[operand]:
                        _require(expr, all(other is operand or _literal(other) or isinstance(other, th.THIRName)
                                           for other in operands), "order-sensitive eager operands")
                writing = any(self.writes[o] for o in operands)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                call = self.call(expr)
                result = call.summary.borrowed_result
                _require(expr, result is not None and result.type == typ, "call view result mismatch")
                writing = self.call_writes(expr)
                plan = PViewCall(expr.loc, typ, call)
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                element = self.element_access(expr)
                member = element.member
                _require(expr, member.cls is MIRClass.OWNED_LEAF and expr.form is th.Form.BORROW
                         and view_compatible(holder, member.type), "view source type mismatch")
                writing = self.writes[expr.index]
                plan = PElementValue(expr.loc, typ, element, True)
            case th.THIRFieldAccess():
                # The loan a view member stores, read whole.
                found = self.field(expr)
                _require(expr, found.fact == typ, "view source type mismatch")
                writing = self.writes.get(expr.receiver, False)
                plan = PViewField(expr.loc, typ, found.place)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        self.types[expr] = typ
        self.writes[expr] = writing
        return plan

    def tuple_alias(self, stmt: th.THIRVarDecl) -> PTupleAlias:
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
        layout = self.tuple_expr(stmt.init, allow_owned=True).layout
        _require(stmt, fact.layout == layout and layout.owns_records, "tuple alias layout or access mismatch")
        self.tuple_roots[stmt.name] = self.tuple_roots[fact.source]
        # The alias names the source's own slot.
        self.bind(stmt.name, stmt.resolved_type, self.scope[fact.source].plan.shape, fact=layout)
        return PTupleAlias(stmt.name, fact.source)

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
        reference = self.references.get(name) if name is not None else None
        return reference.type if isinstance(reference, th.THIRBorrowedRecord) else None

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

    def value(self, expr: th.THIRExpr, expected: TpyType | None = None) -> object:
        """Check an operand: an owned leaf read through a borrow, else a
        scalar value (`expr`)."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        if view_leaf(typ):
            return self.view_value(expr, typ)
        if owned_leaf(typ):
            return self.owned_value(expr, sink=False, expected=expected)
        return self.expr(expr, expected)

    def owned_value(self, expr: th.THIRExpr, *, sink: bool, expected: TpyType | None = None) -> object:
        """Check an owned-leaf expression. At a sink its value lands in the
        destination's storage; as an operand a name or a literal is borrowed
        in place, and anything else is built into a temporary of the
        enclosing full expression, which that expression's region ends."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        _require(expr, owned_leaf(typ), "unsupported owned-leaf expression type")
        _require(expr, self.types.get(expr, typ) == typ, "literal adopts two types")
        fresh = True
        writing = False

        def copy_may_raise() -> object:
            return type_def_of(typ).copy_may_raise
        match expr:
            case th.THIRModuleVar():
                _plain(expr, {"cpp", "global_binding"})
                self.global_binding(expr)
                fresh = False
                plan: object = POwnedPlace(expr.loc, typ, _global_place(expr), copy_may_raise())
            case th.THIRName() if expr.global_binding is not None:
                _plain(expr, {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
                fresh = False
                plan = POwnedPlace(expr.loc, typ, _global_place(expr), copy_may_raise())
            case th.THIRName():
                # The binding, not the name's form, says whether it is storage or a borrow.
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, self.owned_leaf_bound(expr.name) is not None and self.bindings.get(expr.name) == typ,
                         "non-local name")
                fresh = False
                plan = POwnedPlace(expr.loc, typ, PNamePlace(expr.name), copy_may_raise())
            case th.THIRStrLiteral():
                _plain(expr, {"value"})
                _require(expr, expr.form is th.Form.VALUE and owned_constant(typ, expr.value),
                         "unsupported literal value")
                fresh = False
                plan = POwnedLiteral(expr.loc, typ, expr.value, True)
            case th.THIRBytesLiteral():
                _plain(expr, {"value"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
                # A borrow-form literal is a static span; a storage-form one builds a buffer.
                _require(expr, expr.form is th.Form.STORAGE or expr.form is th.Form.BORROW and not sink,
                         "borrowed bytes literal at an owning sink")
                fresh = expr.form is th.Form.STORAGE
                plan = POwnedLiteral(expr.loc, typ, expr.value, expr.form is th.Form.BORROW)
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
                plan = POwnedLiteral(expr.loc, typ, expr.value, False)
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                _require(expr, expr.op not in ("&&", "||") and expr.certified_op, "uncertified binary operation")
                left, right = self.promotion(expr, *self.operands(expr))
                if ((self.writes[expr.left] and not _literal(expr.right))
                        or (self.writes[expr.right] and not _literal(expr.left))):
                    raise MIRUnsupported(expr, "order-sensitive eager operands")
                writing = self.writes[expr.left] or self.writes[expr.right]
                plan = POperation(expr.loc, typ, expr.op, (left, right), owned=True)
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                operand = self.value(expr.operand)
                writing = self.writes[expr.operand]
                plan = POperation(expr.loc, typ, expr.resolved.method.name, (operand,), owned=True)
            case th.THIRCoerce() if expr.owned_passthrough:
                # The source's own storage, read as its family's owned type: lent
                # at a borrowing sink, copied at an owning one.
                _plain(expr, {"expr", "coercion_name"})
                inner = self.lent_value(expr.expr)
                fresh = False
                writing = self.writes[expr.expr]
                plan = POwnedPassthrough(expr.loc, typ, inner, _family_view(typ), copy_may_raise())
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name", "wrap"})
                if (refusal := expr.conversion_refusal) is not None:
                    raise MIRUnsupported(expr, refusal)
                operand = self.value(expr.expr)
                writing = self.writes[expr.expr]
                plan = PConvert(expr.loc, typ, expr.coercion_name, operand, owned=True)
            case th.THIRFormConvert() if view_leaf(expr.value.result_type):
                # A view at an owning sink: a copy of what it views.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW,
                         "unsupported form conversion")
                source = self.view_value(expr.value, expr.value.result_type)
                _require(expr, view_compatible(expr.value.result_type, typ), "form conversion type mismatch")
                writing = self.writes[expr.value]
                plan = POwnedFormConvert(expr.loc, typ, source, copy_may_raise())
            case th.THIRFormConvert():
                # Only the copy of a borrowed read into owned storage: moves,
                # materialized views and generic conversions are not leaf values.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW
                         and isinstance(expr.value, (th.THIRName, th.THIRModuleVar)), "unsupported form conversion")
                source = self.owned_value(expr.value, sink=False)
                _require(expr, source.type == typ, "form conversion type mismatch")
                plan = POwnedFormConvert(expr.loc, typ, source, copy_may_raise())
            case th.THIRStrSlice() if expr.stepped:
                raise MIRUnsupported(expr, "stepped slice")
            case th.THIRMove():
                raise MIRUnsupported(expr, "owned-leaf move")
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                # The callee returns an owned leaf by value: fresh storage for the
                # caller, unless a stub's result may borrow what the call lends --
                # a borrow of those arguments, copied at an owning sink.
                call = self.call(expr)
                summary = call.summary
                result = summary.borrowed_result
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
                         and (result is None or result.type == typ and result.readonly),
                         "call owned result mismatch")
                fresh = result is None
                writing = self.call_writes(expr)
                plan = POwnedCall(expr.loc, typ, call, result is not None,
                                  copy_may_raise() if result is not None else None)
            case th.THIRFieldAccess():
                # A field place: borrowed in place as an operand, copied at a sink.
                found = self.field(expr)
                _require(expr, found.fact == typ, "owned-leaf field type mismatch")
                fresh = False
                plan = POwnedField(expr.loc, typ, found.place, copy_may_raise())
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                # An element place, like a field: borrowed in place as an
                # operand, copied at a sink.
                element = self.element_access(expr)
                member = element.member
                _require(expr, member.cls is MIRClass.OWNED_LEAF and member.type == typ,
                         "owned-leaf element type mismatch")
                fresh = False
                writing = self.writes[expr.index]
                plan = PElementValue(expr.loc, typ, element, False)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                call = self.method_call(expr)
                summary = call.summary
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
                         and summary.borrowed_result is None, "call owned result mismatch")
                writing = self.call_writes(expr)
                plan = POwnedCall(expr.loc, typ, call, False, None)
            case _:
                raise MIRUnsupported(expr, "unsupported owned-leaf expression")
        if fresh and not sink:
            _require(expr, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append(expr)
        self.leaf_layout(expr, typ)
        self.types[expr] = typ
        self.writes[expr] = writing
        return plan

    def record_value(self, expr: th.THIRExpr, typ: NominalType, *, temporary: bool = False,
                     call: bool = False) -> PConstruct | PRecordCall | PRecordMove | PRecordCopy:
        """Check what record storage of type `typ` takes: a construct, a copy
        of a record source (`record_source`: spelled `copy(x)`, or a borrowed
        name, a member read or a form conversion THIR leaves to its form), a
        move of a fixed owned local, or -- where `call` admits it -- a call
        handing over an owned record result."""
        definition = self.definitions.get(expr, typ)
        self.records[typ] = definition
        _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == typ,
                 "record initializer type mismatch")
        writing = False
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
                    _require(expr, self.active_temporaries is not None, "temporary needs full-expression boundary")
                    self.active_temporaries.append(expr)
                params = definition.constructor.params
                _require(expr, len(expr.args) == len(params), "incomplete constructor arguments")
                arguments: list[tuple[str, object]] = []
                for arg, param in zip(expr.args, params):
                    if owned_parameter(param):
                        # Lent to a member that copies it, or handed over for one to move from.
                        operand = self.owned_argument(arg, owned_value_type(param.type), param.passing)
                    elif (record := owned_record_parameter(param)) is not None:
                        # Handed over: a temporary of the full expression the member moves from.
                        operand = self.record_operand(arg, record)
                    elif record_parameter(param):
                        # Lent: a holder the member copies through, a temporary bound to it.
                        operand = self.lent_record_operand(arg, param.borrowed_record.type)
                    elif view_leaf(param.type):
                        # A view parameter takes the loan its argument holds.
                        operand = self.view_value(arg, param.type)
                    elif (layout := optional_parameter(param)) is not None:
                        # An Optional parameter: a pointer at a payload the member copies, or a scalar.
                        operand = self.optional_operand(arg, param.type, layout)
                    else:
                        operand = self.expr(arg, param.type)
                        _require(arg, operand.type == param.type, "constructor argument type")
                    _require(arg, not self.writes[arg], "effectful constructor argument")
                    arguments.append((param.name, operand))
                plan: PConstruct | PRecordCall | PRecordMove | PRecordCopy = PConstruct(
                    expr.loc, typ, tuple(arguments), self.members_plan(definition.initializers))
            case th.THIRCopy():
                _plain(expr, {"value", "cpp_type"})
                source = self.record_source(expr.value)
                _require(expr, source.type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable and expr.form is th.Form.STORAGE,
                         "record is not copyable or copy form")
                plan = PRecordCopy(typ, source)
            case th.THIRMove():
                _plain(expr, {"value"})
                name = self.reference_name(expr.value)
                _require(expr, self.references[name].type == typ, "record source type mismatch")
                _require(expr, definition.layout.movable and name in self.fixed_owned
                         and not self.references[name].readonly
                         and expr.form is expr.value.form, "move needs fixed movable owned local")
                plan = PRecordMove(typ, name)
            case (th.THIRName() | th.THIRSelf()) if expr.form is th.Form.BORROW:
                # A borrowed name read into record storage: C++ copies what
                # it names (sema warned about the inline copy).
                source = self.record_source(expr)
                _require(expr, source.type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
                plan = PRecordCopy(typ, source)
            case th.THIRFieldAccess() if expr.form is th.Form.STORAGE:
                # A member read into record storage: a copy of that member.
                source = self.record_source(expr)
                _require(expr, source.type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
                plan = PRecordCopy(typ, source)
            case th.THIRFormConvert():
                # A local's storage converted into storage: a copy of it.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and isinstance(expr.value, (th.THIRName, th.THIRSelf))
                         and expr.value.form is th.Form.BORROW, "unsupported form conversion")
                source = self.record_source(expr.value)
                _require(expr, source.type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
                plan = PRecordCopy(typ, source)
            case th.THIRCall() | th.THIRMethodCall() if call and _user_call(expr):
                call_plan = self.call(expr, temporary=temporary)
                summary = call_plan.summary
                # The callee's contract, not the destination, says whether the
                # result is fresh: a borrowed result would be copied from its
                # referent. The summary's borrowed result is the signature's
                # (a follows-receiver one differs only in access), so
                # `hands_over` decides; this check only names the refusal.
                _require(expr, summary.borrowed_result is None, "owned result from a borrowed call")
                _require(expr, summary.callee.signature.hands_over(typ) and expr.form is th.Form.STORAGE,
                         "call record result mismatch")
                writing = self.call_writes(expr)
                plan = PRecordCall(expr.loc, typ, call_plan)
            case _:
                raise MIRUnsupported(expr, "unsupported record initializer")
        self.writes[expr] = writing
        return plan

    def members_plan(self, initializers: tuple[MIRFieldInitializer, ...]) -> PMembers:
        """What a construct through a verified definition builds each member
        from: a parameter's argument, a member built by its own constructor
        into storage of the full expression, a literal a view member stores,
        an inert constant, or an Optional record member's holder."""
        members: list[object] = []
        payloads: list[TpyType] = []
        for init in initializers:
            payload = (unwrap_readonly(init.field.type).inner
                       if value_class(init.field.type) is MIRClass.OPTIONAL else None)
            if payload is not None:
                payloads.append(payload)
                if not isinstance(init.source, str):
                    fact = th.THIRBorrowedRecord(payload, True)
                    composed = ((self.shape(payload, storage=True), self.shape(payload, fact),
                                 self.members_plan(init.source.initializers))
                                if isinstance(init.source, MIRComposedConstruct) else None)
                    members.append(PMemberOptional(
                        self.shape(init.field.type, optional_layout=th.THIROptionalLayout(fact)), composed))
                    continue
            if isinstance(init.source, MIRComposedConstruct):
                members.append(PMemberComposed(self.shape(unwrap_readonly(init.field.type), storage=True),
                                               self.members_plan(init.source.initializers)))
            elif isinstance(init.source, str):
                members.append(PMemberArgument(init.source))
            elif init.mode is MIRMemberInitMode.BORROW:
                view = init.field.type
                members.append(PMemberView(init.loc, self.shape(view, _view_holder_fact(view)), init.source))
            else:
                members.append(PMemberConstant(init.loc, init.field.type, init.source))
        return PMembers(tuple(members), tuple(payloads), any(
            init.may_raise for init in initializers if not isinstance(init.source, MIRComposedConstruct)))

    def optional_operand(self, arg: th.THIRExpr, typ: TpyType, layout: th.THIROptionalLayout) -> POptionalOperand:
        """An argument an Optional parameter of type `typ` binds, at a call
        and a constructor alike. A record payload (`P | None`, a pointer):
        null, the address of a record source or of a temporary built for the
        call, an Optional field's payload (`optional_to_ptr`), or an Optional
        holder's own pointer; what the callee writes through the payload and
        whether its result borrows it is its summary's to say. A scalar
        payload (`int32 | None`) takes what an Optional declaration takes."""
        self.optional_layout(arg, layout, typ)
        target = layout.payload
        shape = self.shape(typ, optional_layout=layout)
        if not isinstance(target, th.THIRBorrowedRecord):
            value = self.optional_source(arg, layout)
            self.writes.setdefault(arg, False)
            # The holder the payload is built into is a temporary of the full
            # expression, so a loop condition initializes it once per pass.
            _require(arg, self.active_temporaries is not None, "temporary needs full-expression boundary")
            self.active_temporaries.append(arg)
            return POptionalOperand(arg.loc, shape, True, value)
        match arg:
            case th.THIRArgTemp():
                value = self.argument_temporary(arg, target, addr_of=True)
            case th.THIROptionalPtrArg(value=None):
                _plain(arg, set())
                value = PAbsent()
            case th.THIROptionalPtrArg(lift=True):
                _plain(arg, {"value", "lift"})
                found = self.field(arg.value) if isinstance(arg.value, th.THIRFieldAccess) else None
                _require(arg, found is not None and isinstance(found.fact, th.THIROptionalLayout),
                         "unsupported optional argument")
                self.payload_compatible(arg, found.fact.payload, target)
                value = POptionalFieldCopy(found.place)
            case th.THIROptionalPtrArg():
                _plain(arg, {"value", "addr_of"})
                _require(arg, isinstance(arg.value, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)),
                         "unsupported optional argument")
                value = self.record_source(arg.value)
                self.payload_compatible(arg, value.record, target)
            case th.THIRName() if arg.name in self.optionals:
                # A narrowed name still passes its holder's pointer.
                extract = arg.optional_read is not None and arg.optional_read.extract
                self.payload_compatible(arg, self.optional_name(arg, extract=extract).payload, target)
                value = PWrapperCopy(arg.name)
            case _:
                raise MIRUnsupported(arg, "unsupported optional argument")
        _require(arg, arg.form is th.Form.BORROW, "unsupported optional argument")
        self.writes[arg] = False
        return POptionalOperand(arg.loc, shape, False, value)

    def record_operand(self, arg: th.THIRExpr, typ: NominalType) -> PRecordTemporary:
        """A record argument built into storage of its full expression: a
        construct, a call handing over its owned result, a copy, or a fixed
        owned local moved out."""
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRCopy, th.THIRMove)) or _user_call(arg),
                 "constructor argument type")
        # The temporary stores the loans its construct, the callee's
        # transfers or the copied or moved object name.
        _require(arg, self.active_temporaries is not None, "temporary needs full-expression boundary")
        value = self.record_value(arg, typ, call=True)
        self.active_temporaries.append(arg)
        return PRecordTemporary(arg.loc, typ, self.shape(typ, storage=True), value)

    def lent_record_operand(self, arg: th.THIRExpr, typ: NominalType) -> PRecordSource | PLentTemporary:
        """A record argument lent to a constructor parameter: a record
        source in place (a name, or a member read through one), or a
        temporary bound for the call (never a move: binding the lent
        parameter moves nothing)."""
        if isinstance(arg, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)):
            source = self.record_source(arg)
            _require(arg, source.type == typ, "constructor argument type")
            self.writes[arg] = False
            return source
        _require(arg, not isinstance(arg, th.THIRMove), "constructor argument type")
        return PLentTemporary(self.record_operand(arg, typ), self.shape(typ, th.THIRBorrowedRecord(typ, True)))

    def owned(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> POwnedRecordDeclare:
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

        def initializer() -> object:
            return self.record_value(stmt.init, fact.type, call=True)
        # A constructor argument may need a temporary (an owned leaf built for
        # it), which the declaration's full expression owns.
        value = self.full_expression(stmt.init, check=initializer)
        hoisted = isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
        if hoisted:
            _require(stmt, self.records[fact.type].layout.movable, "hoisted backing needs movable record")
        self.owned_records.add(stmt.name)
        binding = self.bind(stmt.name, fact.type, self.shape(fact.type, fact), self.shape(fact.type, storage=True))
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.STORAGE
                and stmt.name not in self.fn.layout.reassigned_locals):
            self.fixed_owned.add(stmt.name)
        # A hoisted backing's storage is the body's, initialized at its own site.
        return POwnedRecordDeclare(stmt.loc, binding, value, self.placement(stmt), hoisted,
                                   self.missing_placement(stmt, True))

    def replacement(self, stmt: th.THIRAssign) -> PReplacement:
        _plain(stmt, {"target", "value", "rebind_storage", "slot_cpp"})
        name = self.reference_name(stmt.target)
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                 "unsupported replacement storage")
        _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE
                 or not self.references[name].readonly, "readonly in-place replacement")
        typ = self.references[name].type

        def value() -> object:
            return self.record_value(stmt.value, typ, call=True)
        # Its argument temporaries end with the replacing full expression.
        replacing = self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[self.references[name].type].layout.movable,
                 "replacement needs movable record")
        # A STORAGE-form local is one sema never rebinds (a rebound local is a
        # rebind-slot pointer local), so `fixed_owned` is a whole-body fact; a
        # reseat of a member would hand a returned backing back stale.
        _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")
        # OWN builds the new referent into storage of its own, IN_PLACE into the old one.
        return PReplacement(stmt.loc, name, replacing, self.shape(typ, storage=True)
                            if stmt.rebind_storage is RebindStorage.OWN else None)

    def member_write(self, stmt: th.THIRAssign) -> PAssign:
        """`ln.a = v`: an inline record member replaced whole, in place --
        the record around it keeps its identity, so the write is an event
        of the member place only. The value is what record storage takes
        (`record_value`); C++ copy- or move-assigns it."""
        _plain(stmt, {"target", "value"})
        found = self.field(stmt.target, write=True)
        member = found.fact
        _require(stmt, isinstance(member, th.THIRBorrowedRecord) and stmt.target.form is th.Form.STORAGE,
                 "record member write needs member storage")
        if holds_loan(member.type):
            # The write fills the member's entries under the object around it.
            self.held_root(stmt.target)

        def value() -> object:
            return self.record_value(stmt.value, member.type, call=True)
        # Its argument temporaries end with the replacing full expression.
        replacing = self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[member.type].layout.movable, "record member replacement needs movable record")
        return PAssign(stmt.loc, found.place, replacing, True)

    def optional_field_write(self, stmt: th.THIRAssign) -> PAssign:
        """`h.p = v` on an Optional record field: the whole field replaced in
        place -- the record around it keeps its identity, so the write is an
        event of the field place, its payload included. The value empties
        it, copies another Optional's payload (a whole Optional field, or an
        Optional holder's payload), or is a record built, handed over or
        copied into storage of the statement and moved into the payload."""
        _plain(stmt, {"target", "value"})
        found = self.field(stmt.target, write=True)
        whole = found.fact
        _require(stmt, isinstance(whole, th.THIROptionalLayout), "optional field write needs the whole field")
        payload = whole.payload.type
        value = stmt.value

        def check() -> POptionalFieldValue:
            if isinstance(value, th.THIRLiteral) and value.value is None:
                _plain(value, {"value", "none_cpp"})
                _require(value, isinstance(value.result_type, (OptionalType, NoneType, VoidType)),
                         "optional absence type")
                self.writes[value] = False
                return POptionalFieldValue(payload, payload, PAbsent())
            if isinstance(value, th.THIRFieldAccess):
                # THIR spells a copy of a narrowed payload as a bare read of it,
                # with no copy node for MIR to build the record from.
                _require(value, not value.narrowed_deref, "optional field copy from a narrowed payload")
                source = self.field(value)
                _require(value, isinstance(source.fact, th.THIROptionalLayout)
                         and source.fact.payload.type == payload, "optional field copy source mismatch")
                place: object = source.place
            elif isinstance(value, th.THIRFormConvert):
                _plain(value, {"value", "is_const"})
                name = value.value
                _require(value, value.form is th.Form.STORAGE and isinstance(name, th.THIRName)
                         and name.name in self.optionals, "unsupported optional field value")
                layout = self.optional_name(name, extract=False)
                _require(value, isinstance(layout.payload, th.THIRBorrowedRecord)
                         and layout.payload.type == payload, "optional field copy source mismatch")
                place = PNamePlace(name.name)
            else:
                # Built, handed over or copied into storage of the statement,
                # then moved into the payload.
                temporary = self.record_operand(value, payload)
                _require(value, self.records[payload].layout.movable, "optional field payload needs movable record")
                return POptionalFieldValue(payload, payload, temporary)
            _require(value, self.records[payload].layout.copyable, "record is not copyable or copy form")
            self.writes[value] = False
            return POptionalFieldValue(payload, payload, place)
        return PAssign(stmt.loc, found.place, self.full_expression(value, check=check), True)

    def view_member_write(self, stmt: th.THIRAssign) -> PViewMemberAssign:
        """`t.s = v`: a view member rebound to the loan `v` holds. The record
        keeps its identity and no storage is replaced; every object the
        place may reach stores the loan (`dependencies.store_loan`)."""
        _plain(stmt, {"target", "value"})
        found = self.field(stmt.target, write=True)
        typ = found.fact
        # A loan held by a temporary of the statement ends with it, which
        # the scope-end analysis reports.
        return PViewMemberAssign(stmt.loc, found.place,
                                 self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, typ)))

    def reference(self, node: object, ref: th.THIRBorrowedRecord, typ: TpyType) -> None:
        _require(node, isinstance(ref, th.THIRBorrowedRecord), "invalid reference fact")
        problem = reference_problem(ref, typ)
        _require(node, problem is None, problem or "reference type mismatch")

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
        if name is not None and (reference := self.references.get(name)) is not None:
            self.held_layout(place, reference.type)

    def held_layout(self, node: object, typ: NominalType) -> MIRRecordLayout:
        """The layout of a record the body reaches through a holder, which a
        caller need not be able to construct (`MIRDefinitions.held_layout`)."""
        if typ not in self.records:
            self.records[typ] = self.definitions.held_layout(node, typ)
        return self.records[typ].layout

    def wrapped_record(self, node: object, typ: TpyType) -> None:
        """A record a wrapper holds (an Optional or union payload, a tuple
        member, an optional backing): one whose fields hold a borrow would
        need the wrapper's selection to reach its stored loans."""
        _require(node, not holds_loan(typ), "wrapper holds a borrow")

    def container_members(self, node: object, typ: TpyType) -> tuple[MIRShape, ...]:
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
        self.members[bare] = definition.members
        return definition.members

    def unpublished_container(self, node: object, typ: TpyType) -> None:
        """A container binding THIR published no fact for: its members'
        refusal when they have one (an element holding a borrow, a nested
        container), else the missing fact."""
        bare = native_container_subject(typ)
        if isinstance(bare, NominalType) and native_container_type(bare):
            self.container_members(node, bare)
            raise MIRUnsupported(node, "missing native container fact")

    def container(self, node: object, fact: th.THIRNativeContainer, typ: TpyType) -> None:
        """Check a container fact against its type and the members the type
        derives; the fact's element is the member iteration yields. An
        `Own[...]` parameter publishes the fact for the container it owns."""
        bare = native_container_subject(typ)
        _require(node, isinstance(fact, th.THIRNativeContainer) and isinstance(bare, NominalType)
                 and fact.type == bare and type(fact.readonly) is bool
                 and (not isinstance(unwrap_ref_type(typ), ReadonlyType) or fact.readonly),
                 "invalid native container fact")
        _require(node, native_container_type(bare), "unsupported native container type")
        members = self.container_members(node, bare)
        member, expected = fact.element, members[0]
        if isinstance(member, th.THIRBorrowedRecord):
            self.reference(node, member, member.type)
            _require(node, expected.cls is MIRClass.RECORD and member.type == expected.type
                     and member.readonly == fact.readonly, "unsupported native record element")
        else:
            _require(node, expected.cls is not MIRClass.RECORD and member == expected.type,
                     "unsupported native scalar element")

    def container_name(self, expr: th.THIRExpr) -> th.THIRNativeContainer:
        _require(expr, isinstance(expr, th.THIRName), "container source needs local name")
        _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect"})
        fact = self.containers.get(expr.name)
        _require(expr, fact is not None and expr.form is th.Form.BORROW
                 and expr.name not in self.fn.layout.reassigned_locals, "container source needs fixed borrowed binding")
        self.container(expr, fact, expr.result_type)
        return fact

    def container_receiver(self, expr: th.THIRExpr) -> bool:
        """Whether `expr` names a container place: a borrowed, owned or
        viewed container binding, a container field, or a container view a
        method stub returns."""
        match expr:
            case th.THIRName() if expr.global_binding is None:
                return (expr.name in self.containers or self.owned_container_bound(expr.name)
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

    def container_holder(self, typ: NominalType, members: tuple[MIRShape, ...], readonly: bool) -> MIRShape:
        """The shape of a holder of a borrowed container place (a field, a
        call's result), its element fact taken from the place's members."""
        element = members[0]
        fact = th.THIRNativeContainer(typ, th.THIRBorrowedRecord(element.type, element.readonly)
                                      if element.cls is MIRClass.RECORD else element.type, readonly)
        return self.shape(typ, container=fact)

    def container_place(self, expr: th.THIRExpr) -> PContainer:
        """Check a container place expression: its type, its members under
        the access the place has, and whether that access is readonly."""
        match expr:
            case th.THIRName() if expr.name in self.containers:
                fact = self.container_name(expr)
                return PContainer(fact.type, with_access(self.members[fact.type], fact.readonly), fact.readonly,
                                  PNamePlace(expr.name))
            case th.THIRName() if (self.owned_container_bound(expr.name)
                                   or self.container_view_access(expr.name) is not None):
                _plain(expr, {"name", "is_last_use", "is_movable"})
                typ = self.bindings[expr.name]
                # An `Own[...]` parameter's name keeps the wrapper THIR gave the
                # parameter; the storage it names is the bare container.
                _require(expr, native_container_subject(expr.result_type) == typ
                         and (expr.form in (th.Form.BORROW, th.Form.VALUE)
                              or expr.form is th.Form.STORAGE and self.owned_container_bound(expr.name)),
                         "container name type mismatch")
                readonly = self.container_view_access(expr.name) or False
                return PContainer(typ, with_access(self.members[typ], readonly), readonly, PNamePlace(expr.name))
            case th.THIRFieldAccess():
                found = self.field(expr)
                fact = found.fact
                _require(expr, isinstance(fact, th.THIRNativeContainer), "container field needs container storage")
                members = with_access(self.members[fact.type], fact.readonly)
                return PContainer(fact.type, members, fact.readonly, PContainerField(expr.loc, found.place),
                                  self.container_holder(fact.type, members, fact.readonly))
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr) and self.container_receiver(expr):
                return self.call_container(expr)
            case th.THIRMethodCall() if self.container_receiver(expr):
                return self.view_call(expr)
        raise MIRUnsupported(expr, "container source needs a place")

    def call_container(self, expr: th.THIRCall | th.THIRMethodCall) -> PContainer:
        """A container a user call returns by reference: a place of the
        callee's container (its referents are the summary's return
        origins), at the access the call's result is bound at."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        call = self.call(expr)
        result = self.call_results[expr]
        _require(expr, result is not None and native_container_type(result.type)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == result.type,
                 "call container result mismatch")
        # The emit pass evaluates a place's operands (an index, slice bounds)
        # before its container, so the call must not write what they read.
        _require(expr, not self.call_writes(expr), "order-sensitive eager operands")
        self.writes[expr] = False
        members = with_access(self.members[result.type], result.readonly)
        return PContainer(result.type, members, result.readonly, PContainerCall(expr.loc, call),
                          self.container_holder(result.type, members, result.readonly))

    def element_index(self, expr: th.THIRSubscript, place: PContainer) -> object:
        """Check a subscript's index or key: a fixed-width int position, or
        a key of the dict's key type (an owned-leaf key is lent, a view of
        it lent as the view)."""
        index = expr.index
        _, _, keyed = declared_members(place.type)
        # A key addresses the value member, a position the element; a type
        # keyed by its only member, or whose value member no key reaches, is not modeled.
        _require(index, keyed is place.keyed, "unsupported keyed container")
        if not keyed:
            plan = self.expr(index)
            _require(index, int_traits_of(plan.type) is not None, "element index needs a fixed-width int")
            return plan
        key = place.element.type
        if storage_leaf(key):
            plan = self.expr(index, key)
            _require(index, plan.type == key, "element key type mismatch")
            return plan
        plan = self.lent_value(index)
        read = view_owned_leaf(index.result_type) if view_leaf(index.result_type) else plan.type
        _require(index, through_view(read) == key, "element key type mismatch")
        return plan

    def element_access(self, expr: th.THIRSubscript) -> PElementPlace:
        """Check a container subscript as a whole: its place, its index, and
        that the operands cannot reorder a write. Returns the member it reaches.
        The stub binding a native receiver's read renders through is
        spelling only: the type's element contract is what is modeled."""
        _plain(expr, {"receiver", "index", "bounds_safe", "bound_getitem"})
        _require(expr, expr.tuple_index is None, "tuple projection of a container")
        place = self.container_place(expr.receiver)
        index = self.element_index(expr, place)
        _require(expr, not self.writes[expr.index] or _literal(expr.index), "order-sensitive eager operands")
        # An owned-leaf key is read by the lookup (its hash and its
        # comparisons), so its holder stays live through the access.
        return PElementPlace(index, place.keyed and place.element.cls is not MIRClass.LEAF, place,
                             not expr.bounds_safe or place.keyed)

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
        _require(expr, name in self.references, "unknown reference source")
        reference = self.references[name]
        named = unwrap_readonly(unwrap_ref_type(expr.result_type))
        if qualified and isinstance(expr, th.THIRSelf) and named != reference.type:
            _require(expr, isinstance(named, NominalType) and self.binds_at(expr, reference.type, named),
                     "reference type mismatch")
            return name
        self.reference(expr, reference, expr.result_type)
        return name

    def record_source(self, expr: th.THIRExpr) -> PRecordSource:
        """The record storage an expression names in place, at the access
        it is reached with: a name or `self` (what its holder reaches), or
        a member read through them (`ln.a`, `self.line.a`). Only a copy, a
        returned borrow or a lent argument takes a member; reseats, aliases
        and wrapper captures keep needing a name (`reference_name`)."""
        if isinstance(expr, th.THIRFieldAccess):
            _require(expr, expr.form is th.Form.STORAGE, "record member read form")
            found = self.field(expr)
            _require(expr, isinstance(found.fact, th.THIRBorrowedRecord), "record source needs record storage")
            return PRecordSource(expr.loc, found.fact, found.place, False, True, self.shape(found.fact.type, found.fact))
        if isinstance(expr, th.THIRName) and expr.name in self.optionals:
            # A narrowed Optional name: the record its payload points at.
            source = self.optional_name(expr, extract=True).payload
            _require(expr, isinstance(source, th.THIRBorrowedRecord), "record source needs record storage")
            return PRecordSource(expr.loc, source, PNamePlace(expr.name, True), False, False,
                                 self.shape(source.type, source))
        name = self.reference_name(expr)
        # The name's own holder points at it and lends it.
        held = self.references[name]
        return PRecordSource(expr.loc, held, PNamePlace(name), True, False, self.shape(held.type, held))

    def borrowed_expression(self, expr: th.THIRExpr, result: th.THIRBorrowedRecord) -> object:
        with self.argument_order(expr):
            if isinstance(expr, th.THIRFieldAccess):
                # A member of record storage the result reaches, borrowed in
                # place (THIR reads it in STORAGE form, as a storage borrow's
                # source); never lent with more access than its path has.
                self.reference(expr, result, expr.result_type)
                source = self.record_source(expr)
                _require(expr, source.type == result.type and (not source.record.readonly or result.readonly),
                         "return increases access")
                return PBorrowedField(expr.loc, source.place, self.shape(result.type, result))
            return self._borrowed_expression(expr, result)

    def _borrowed_expression(self, expr: th.THIRExpr, result: th.THIRBorrowedRecord) -> object:
        _require(expr, expr.form is (th.Form.VALUE if _user_call(expr) else th.Form.BORROW),
                 "unsupported borrowed expression form")
        self.reference(expr, result, expr.result_type)
        match expr:
            case th.THIRSlotEmplace():
                # The emplacement is the select arm itself: its storage is built where it is chosen.
                return self.anchored(expr, self.select_temporary(expr, result))
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                call = self.call(expr)
                actual = self.call_results[expr]
                _require(expr, actual is not None and actual.type == result.type
                         and (not actual.readonly or result.readonly), "call result increases access")
                return PBorrowedCall(expr.loc, call, self.shape(result.type, result))
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                cond = self.expr(expr.cond)
                _require(expr, cond.type == BOOL, "condition requires bool")
                then = self.anchored(expr.then, self._borrowed_expression(expr.then, result))
                orelse = self.anchored(expr.orelse, self._borrowed_expression(expr.orelse, result))
                return PBorrowedSelect(expr.loc, cond, then, orelse, self.shape(result.type, result))
            case _:
                name = self.reference_name(expr)
                source = self.references[name]
                _require(expr, not source.readonly or result.readonly, "return increases access")
                return PBorrowedName(expr.loc, name)

    def borrowed_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> PBorrowedBinding:
        if declaration:
            allowed = {"name", "resolved_type", "init", "cpp_type", "is_const"}
            if isinstance(stmt, th.THIRVarDecl):
                allowed.add("cpp_local_representation")
                _require(stmt, stmt.form is th.Form.BORROW, "call binding needs borrowed form")
            else:
                allowed.add("kind")
                _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported call binding")
            _plain(stmt, allowed)
            fact = th.THIRBorrowedRecord(unwrap_readonly(unwrap_ref_type(stmt.resolved_type)), stmt.is_const)
            self.reference(stmt, fact, stmt.resolved_type)
            source = stmt.init
        else:
            _plain(stmt, {"name", "kind", "value"})
            _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR and stmt.name not in self.parameters,
                     "unsupported call reseat")
            fact = self.references.get(stmt.name)
            _require(stmt, fact is not None, "missing call reseat destination")
            source = stmt.value
        value = self.borrowed_expression(source, fact)
        binding = None
        if declaration:
            binding = self.bind(stmt.name, fact.type, self.shape(fact.type, fact))
        else:
            _require(stmt, stmt.name not in self.fixed_owned, "fixed owned local reseated")
        return PBorrowedBinding(stmt.loc, stmt.name, binding, value, stmt)

    def borrow_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> PBorrowBinding:
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
            _require(stmt, stmt.is_const == fact.readonly, "alias access mismatch")
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
        if storage:
            if isinstance(source, th.THIRSubscript):
                _require(source, declaration and isinstance(source.receiver, th.THIRName)
                         and source.receiver.name in self.parameters, "tuple borrow needs parameter capture")
                found = self.projection(source, capture=True)
            else:
                _require(source, isinstance(source, th.THIRFieldAccess) and source.form is th.Form.STORAGE,
                         "storage borrow needs record field")
                found = self.field(source)
            reference = found.fact
            _require(source, isinstance(reference, th.THIRBorrowedRecord), "storage borrow needs record")
            place = found.place
        else:
            source_name = self.reference_name(source)
            _require(stmt, alias.source == source_name, "alias source mismatch")
            reference = self.references[source_name]
            place = PNamePlace(source_name)
        self.reference(stmt, fact, reference.type)
        _require(stmt, not reference.readonly or fact.readonly, "alias increases access")
        binding = None
        if declaration:
            binding = self.bind(name, fact.type, self.shape(fact.type, fact))
        else:
            _require(stmt, self.references.get(name) == fact, "alias destination mismatch")
            _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")
        if not storage:
            return PBorrowedBinding(stmt.loc, name, binding, PBorrowedName(source.loc, place.name), stmt)
        # A tuple member a parameter captures points at the record.
        return PBorrowBinding(stmt.loc, name, binding, place, isinstance(place, PTupleElementPlace), stmt)

    def union_layout(self, node: object, layout: th.THIRUnionLayout, typ: TpyType) -> None:
        _require(node, isinstance(layout, th.THIRUnionLayout)
                 and isinstance(layout.type, UnionType)
                 and layout.type == unwrap_readonly(unwrap_ref_type(typ))
                 and len(layout.elements) == len(layout.type.members), "unsupported union layout")
        kinds = set()
        for member, alternative in zip(layout.elements, layout.type.members):
            if member is None:
                _require(node, is_void_like_type(alternative), "union absence type mismatch")
            elif isinstance(member, th.THIRBorrowedRecord):
                self.reference(node, member, alternative)
                self.wrapped_record(node, member.type)
                _require(node, member.readonly or (unwrap_readonly(typ) == typ
                         and unwrap_readonly(alternative) == alternative), "union layout increases access")
                kinds.add("reference")
            else:
                _require(node, storage_leaf(member) and member == alternative, "unsupported union scalar")
                kinds.add("value")
        _require(node, len(kinds) == 1 and len(layout.elements) >= 2, "mixed or empty union layout")

    def union_name(self, expr: th.THIRName) -> th.THIRUnionLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "union_read", "indirect"})
        fact = expr.union_read
        _require(expr, isinstance(fact, th.THIRUnionLayout) and self.unions.get(expr.name) == fact,
                 "missing or inconsistent union read")
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == fact.type,
                 "union read type or form mismatch")
        return fact

    def union_source(self, expr: th.THIRExpr, target: th.THIRUnionLayout,
                      literal: th.THIRUnionLiteral | None = None) -> object:
        """What a union binding takes, with the alternative it engages."""
        node = expr
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
            return PWrapperLiteral(node.loc, literal.alternative, member, literal.value)
        if isinstance(expr, th.THIRName) and expr.name in self.unions:
            source = self.union_name(expr)
            _require(expr, source.type == target.type, "union copy type mismatch")
            for src, dst in zip(source.elements, target.elements):
                if src is None or dst is None:
                    _require(expr, src is dst, "union copy absence mismatch")
                else:
                    self.payload_compatible(expr, src, dst)
            return PWrapperCopy(expr.name)
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            _require(expr, None in target.elements, "union has no absence alternative")
            return PAbsent(target.elements.index(None))
        if isinstance(expr, th.THIRName) and expr.name in self.references:
            name = self.reference_name(expr)
            source = self.references[name]
            matches = [i for i, m in enumerate(target.elements)
                       if isinstance(m, th.THIRBorrowedRecord) and m.type == source.type]
            _require(expr, len(matches) == 1, "union has no matching record alternative")
            self.payload_compatible(expr, source, target.elements[matches[0]])
            return PWrapperCapture(name, matches[0])
        plan = self.expr(expr)
        _require(expr, plan.type in target.elements and not self.writes[expr], "unsupported union member construction")
        return PWrapperValue(plan, target.elements.index(plan.type))

    def union_extraction(self, node: object, fact: th.THIRUnionExtraction | None) -> TpyType | th.THIRBorrowedRecord:
        _require(node, isinstance(fact, th.THIRUnionExtraction)
                 and self.unions.get(fact.source) == fact.layout,
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

    def optional_layout(self, node: object, layout: th.THIROptionalLayout, typ: TpyType) -> None:
        problem = optional_layout_problem(typ, layout)
        _require(node, problem is None, problem or "unsupported optional layout")
        if isinstance(layout.payload, th.THIRBorrowedRecord):
            self.wrapped_record(node, layout.payload.type)

    def optional_name(self, expr: th.THIRName, *, extract: bool) -> th.THIROptionalLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "deref", "indirect", "optional_read"})
        fact = expr.optional_read
        _require(expr, isinstance(fact, th.THIROptionalRead) and fact.extract is extract
                 and expr.name in self.optionals and fact.layout == self.optionals[expr.name],
                 "missing or inconsistent optional read")
        layout = fact.layout
        reference = isinstance(layout.payload, th.THIRBorrowedRecord)
        typ = layout.payload.type if reference else layout.payload
        actual = unwrap_readonly(unwrap_ref_type(expr.result_type))
        _require(expr, actual == typ if extract else (
            actual in (typ, self.bindings[expr.name]) or isinstance(actual, VoidType)),
            "optional read type mismatch")
        _require(expr, expr.form is (th.Form.BORROW if reference else th.Form.VALUE)
                 and (reference or expr.deref is extract), "optional read form mismatch")
        return layout

    def optional_source(self, expr: th.THIRExpr | None, target: th.THIROptionalLayout) -> object:
        """What an Optional binding takes: nothing (empty), another
        Optional's value, a holder of an Optional field's payload, a record
        binding's holder, or a scalar payload."""
        if expr is None:
            return PAbsent()
        node = expr
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            if isinstance(expr.result_type, OptionalType):
                self.optional_layout(expr, target, expr.result_type)
            else:
                _require(expr, isinstance(expr.result_type, (NoneType, VoidType)),
                         "optional absence type")
            return PAbsent()
        if isinstance(expr, th.THIRName) and expr.name in self.optionals and (
                expr.optional_read is not None and not expr.optional_read.extract):
            source = self.optional_name(expr, extract=False)
            self.payload_compatible(expr, source.payload, target.payload)
            return PWrapperCopy(expr.name)
        if isinstance(expr, th.THIRFormConvert) and isinstance(expr.value, th.THIRFieldAccess):
            # A holder of an Optional record field's inline payload (`optional_to_ptr`).
            _plain(expr, {"value", "is_const"})
            found = self.field(expr.value)
            source = found.fact
            _require(expr, isinstance(source, th.THIROptionalLayout) and expr.form is th.Form.BORROW
                     and isinstance(target.payload, th.THIRBorrowedRecord)
                     and expr.is_const == target.payload.readonly, "unsupported optional field borrow")
            self.payload_compatible(expr, source.payload, target.payload)
            return POptionalFieldCopy(found.place)
        if isinstance(target.payload, th.THIRBorrowedRecord):
            _require(expr, isinstance(expr, th.THIRName), "optional capture needs local name")
            name = self.reference_name(expr)
            source = self.references[name]
            _require(expr, source.type == target.payload.type
                     and (not source.readonly or target.payload.readonly), "optional capture access mismatch")
            return PWrapperCapture(name)
        if _literal(expr):
            # Hoisted assignments contextualize the literal with the wrapper type.
            _require(expr, expr.form is th.Form.VALUE, "unsupported optional literal form")
            payload = target.payload
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS
                         and expr.result_type in (payload, OptionalType(payload)), "unsupported optional literal coercion")
                expr = expr.expr
            _plain(expr, {"value", "int_cpp"})
            typ = literal_type(expr, payload)
            _require(expr, expr.form is th.Form.VALUE and typ in (payload, OptionalType(payload))
                     and leaf_constant(payload, expr.value), "optional literal payload mismatch")
            return PWrapperLiteral(node.loc, None, payload, expr.value)
        plan = self.expr(expr, target.payload)
        _require(expr, plan.type == target.payload and not self.writes[expr],
                 "effectful or mistyped optional payload")
        return PWrapperValue(plan)

    def tuple_layout(self, node: object, layout: th.THIRTupleLayout | None,
                     typ: TpyType, *, allow_owned: bool = False) -> None:
        readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
        typ = unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
        _require(node, isinstance(layout, th.THIRTupleLayout) and isinstance(typ, TupleType),
                 "missing tuple layout")
        _require(node, len(layout.elements) == len(typ.element_types), "tuple layout arity")
        _require(node, allow_owned or not layout.owns_records, "owning tuple position is unsupported")
        for member, element in zip(layout.elements, typ.element_types):
            if isinstance(member, (th.THIRBorrowedRecord, th.THIROwnedRecord)):
                self.reference(node, th.THIRBorrowedRecord(member.type, member.readonly), element)
                self.wrapped_record(node, member.type)
                _require(node, (not readonly and unwrap_readonly(element) == element) or member.readonly,
                         "tuple layout increases access")
                if isinstance(member, th.THIROwnedRecord):
                    definition = self.definitions.get(node, member.type)
                    _require(node, definition.layout.movable, "tuple construction needs movable record")
                    self.records[member.type] = definition
            else:
                _require(node, storage_leaf(member) and member == element,
                         "unsupported tuple scalar")

    def tuple_compatible(self, node: object, source: th.THIRTupleLayout,
                         target: th.THIRTupleLayout) -> None:
        _require(node, len(source.elements) == len(target.elements), "tuple copy arity")
        for src, dst in zip(source.elements, target.elements):
            self.payload_compatible(node, src, dst)

    def payload_compatible(self, node: object, source: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord,
                           target: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> None:
        if isinstance(source, th.THIRBorrowedRecord) and isinstance(target, th.THIRBorrowedRecord):
            _require(node, source.type == target.type and (not source.readonly or target.readonly),
                     "payload copy type or access mismatch")
        else:
            _require(node, source == target, "payload copy type mismatch")

    def tuple_expr(self, expr: th.THIRExpr, *, allow_owned: bool = False) -> PTupleName | PTupleLiteral:
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW, th.Form.STORAGE), "unsupported tuple form")
        elements: list[object] = []
        if isinstance(expr, th.THIRName):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, expr.name in self.tuples, "tuple needs local payload")
            layout = self.tuples[expr.name]
            _require(expr, unwrap_ref_type(unwrap_readonly(unwrap_ref_type(expr.result_type)))
                     == self.bindings[expr.name], "tuple name type mismatch")
        else:
            _require(expr, isinstance(expr, (th.THIRTupleLiteral, th.THIRBorrowTupleLiteral)),
                     "unsupported tuple expression")
            allowed = {"elements", "tuple_layout"}
            if isinstance(expr, th.THIRBorrowTupleLiteral):
                allowed |= {"spelled_cpp", "elem_cpps", "addr_of"}
                _require(expr, len(expr.addr_of) == len(expr.elements), "tuple address arity")
            _plain(expr, allowed)
            layout = expr.tuple_layout
            self.tuple_layout(expr, layout, expr.result_type, allow_owned=allow_owned)
            _require(expr, len(expr.elements) == len(layout.elements), "tuple capture arity")
            for element, member in zip(expr.elements, layout.elements):
                if isinstance(member, th.THIROwnedRecord):
                    _require(element, isinstance(expr, th.THIRTupleLiteral)
                             and isinstance(element, th.THIRCtorCall), "inline tuple record needs constructor")
                    elements.append(self.record_value(element, member.type))
                elif isinstance(member, th.THIRBorrowedRecord):
                    _require(expr, isinstance(expr, th.THIRBorrowTupleLiteral), "value tuple borrows record")
                    name = self.reference_name(element)
                    source = self.references[name]
                    _require(element, source.type == member.type and (not source.readonly or member.readonly),
                             "tuple capture type or access mismatch")
                    elements.append(PNamePlace(name))
                else:
                    plan = self.expr(element, member)
                    _require(element, plan.type == member and not self.writes[element],
                             "effectful or mistyped tuple element")
                    elements.append(plan)
        self.tuple_layout(expr, layout, expr.result_type, allow_owned=allow_owned)
        _require(expr, expr.form is th.Form.STORAGE if layout.owns_records and isinstance(expr, th.THIRName)
                 else expr.form in (th.Form.VALUE, th.Form.BORROW), "tuple form disagrees with layout")
        shape = self.shape(expr.result_type, tuple_layout=layout)
        if isinstance(expr, th.THIRName):
            return PTupleName(expr.loc, layout, shape, expr.name)
        return PTupleLiteral(expr.loc, layout, shape, tuple(elements))

    def projection(self, expr: th.THIRSubscript, *, capture: bool = False) -> '_Field':
        _plain(expr, {"receiver", "index", "tuple_index", "elem_pointer"}
               | ({"deref"} if capture else set()))
        _require(expr, not capture or expr.deref, "tuple capture needs dereferenced element")
        receiver = self.tuple_expr(expr.receiver, allow_owned=True)
        layout = receiver.layout
        _require(expr, not layout.owns_records or isinstance(expr.receiver, th.THIRName),
                 "owned tuple projection needs existing local")
        index = expr.tuple_index
        _require(expr, type(index) is int and 0 <= index < len(layout.elements),
                 "missing or invalid tuple index")
        _require(expr, isinstance(expr.index, th.THIRLiteral)
                 and type(expr.index.value) is int and expr.index.value == index,
                 "tuple index disagreement")
        _plain(expr.index, {"value", "int_cpp"})
        member = layout.elements[index]
        # The render's pointer fact must agree with the layout MIR reads.
        _require(expr, not expr.elem_pointer
                 or isinstance(member, th.THIRBorrowedRecord),
                 "tuple element pointer disagreement")
        if isinstance(member, (th.THIRBorrowedRecord, th.THIROwnedRecord)):
            self.reference(expr, th.THIRBorrowedRecord(member.type, member.readonly), expr.result_type)
            _require(expr, expr.form is th.Form.BORROW, "tuple reference projection form")
            if isinstance(member, th.THIROwnedRecord):
                _require(expr, not capture, "owned tuple element capture is unsupported")
        else:
            _require(expr, not capture, "tuple capture needs record element")
            _require(expr, expr.result_type == member and expr.form is th.Form.VALUE,
                     "tuple scalar projection type or form")
        root = expr.receiver.name if isinstance(expr.receiver, th.THIRName) else receiver
        return _Field(member, PTupleElementPlace(root, index))

    def field(self, expr: th.THIRFieldAccess, *, write: bool = False) -> '_Field':
        """A field place. An Optional record field (`optional_record_field`)
        is the whole field, at the access its path reaches it with
        (`THIROptionalLayout`), or its payload record once THIR narrowed the
        read (`narrowed_deref`)."""
        _require(expr, not expr.opt_deref_check, "unproven optional field read")
        fact = expr.field_identity
        payload = optional_record_field(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
        _plain(expr, {"receiver", "field_cpp", "field_identity", "is_arrow"}
               | ({"narrowed_deref"} if payload is not None else set()))
        match expr.receiver:
            # A construct names its storage or refuses; a call only when it hands one over.
            case receiver if isinstance(receiver, th.THIRCtorCall) or th.record_rvalue_storage(receiver) is not None:
                _require(expr, not write and not expr.is_arrow, "temporary field requires scalar read")
                reference, base = self.temporary(expr.receiver)
            case th.THIRFieldAccess():
                # A store under a narrowed payload would replace part of it while
                # MIR keeps the payload whole (`place_info` forbids it).
                _require(expr, not (write and expr.receiver.narrowed_deref), "optional field payload write")
                reference, base = self.field(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs record storage")
            case th.THIRNarrowedRead():
                reference = self.inline_union(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs union record")
                extraction = expr.receiver.union_extraction
                base = PUnionPayloadPlace(extraction.source, extraction.alternative)
            case th.THIRName() if expr.receiver.name in self.optionals:
                reference = self.optional_name(expr.receiver, extract=True).payload
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs optional record")
                base = PNamePlace(expr.receiver.name, True)
            case th.THIRSubscript() if expr.receiver.tuple_index is None:
                # A field of a record element, read in place.
                base = self.element_access(expr.receiver)
                member = base.member
                _require(expr, member.cls is MIRClass.RECORD and expr.receiver.form is th.Form.BORROW
                         and unwrap_readonly(unwrap_ref_type(expr.receiver.result_type)) == member.type,
                         "field needs record element")
                reference = th.THIRBorrowedRecord(member.type, member.readonly)
            case th.THIRSubscript():
                reference, base = self.projection(expr.receiver)
                _require(expr, isinstance(reference, (th.THIRBorrowedRecord, th.THIROwnedRecord)),
                         "field needs tuple reference")
            case _:
                name = self.reference_name(expr.receiver, qualified=True)
                reference = self.references[name]
                base = PNamePlace(name)
        _require(expr, isinstance(fact, th.THIRFieldIdentity), "missing field identity")
        # An identity names a declared member of any type, not only one MIR models.
        _require(expr, modeled_field(fact.type), "unsupported field type or form")
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
        readonly = reference.readonly or isinstance(fact.type, ReadonlyType)
        _require(expr, not (write and readonly), "readonly field store")
        # A narrowed Optional field read names its inline payload.
        place = PFieldPlace(base, MIRField(MIRFieldId(fact.owner, fact.name), fact.type), bool(expr.narrowed_deref))
        if payload is not None:
            # The payload record is stored inline: copied, destroyed and
            # replaced with the field, so its verified definition comes along.
            self.record_layout(expr, payload)
            _require(expr, expr.form is th.Form.STORAGE, "unsupported field type or form")
            if expr.narrowed_deref:
                _require(expr, not write, "optional field payload write")
                member = th.THIRBorrowedRecord(payload, readonly)
                self.reference(expr, member, expr.result_type)
                return _Field(member, place)
            # An assign target keeps the narrowed type but writes the whole field.
            result = unwrap_readonly(unwrap_ref_type(expr.result_type))
            _require(expr, result == unwrap_readonly(fact.type) or write and result == payload,
                     "unsupported field type or form")
            return _Field(th.THIROptionalLayout(th.THIRBorrowedRecord(payload, readonly)), place)
        if native_container_type(unwrap_readonly(fact.type)):
            # A container field is owned storage of the record; its region is
            # reached through it, and it is never replaced whole.
            _require(expr, not write, "container field replacement is unsupported")
            bare = unwrap_readonly(fact.type)
            _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == bare
                     and expr.form in (th.Form.STORAGE, th.Form.BORROW), "unsupported field type or form")
            member = self.container_members(expr, bare)[0]
            element = th.THIRBorrowedRecord(member.type, readonly) if member.cls is MIRClass.RECORD else member.type
            return _Field(th.THIRNativeContainer(bare, element, readonly), place)
        if view_leaf(fact.type):
            # A view member stores a loan in the record object, keyed by a
            # member of the record's layout: read whole, or rebound whole
            # (`view_member_write`).
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.BORROW,
                     "unsupported field type or form")
            self.held_layout(expr, reference.type)
            self.held_root(expr)
            return _Field(fact.type, place)
        if owned_leaf(fact.type):
            # The field is a place of the record's storage: its buffer is
            # borrowed, copied out or replaced in place, never projected into.
            # Its form is how the C++ spells the member (a BigInt reads as a
            # value scalar, str as storage); either way it is that lvalue.
            _require(expr, expr.result_type == fact.type and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                     "unsupported field type or form")
            self.leaf_layout(expr, fact.type)
            return _Field(fact.type, place)
        # A readonly-wrapped owned leaf has no field place of its own type.
        _require(expr, owned_value_type(fact.type) is None, "owned-leaf record field")
        if storage_leaf(fact.type):
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.VALUE,
                     "unsupported field type or form")
            return _Field(fact.type, place)
        # An inline record member: a place of the record's storage, borrowed,
        # copied out, or replaced whole in place (`member_write`).
        member = th.THIRBorrowedRecord(unwrap_readonly(fact.type), readonly)
        self.reference(expr, member, expr.result_type)
        return _Field(member, place)

    def temporary(self, expr: th.THIRCtorCall | th.THIRCall | th.THIRMethodCall) -> '_Field':
        """A record rvalue in storage of its full expression: a construct, or
        a call's owned record result."""
        fact = th.record_rvalue_storage(expr)
        _require(expr, self.active_temporaries is not None, "temporary needs full-expression boundary")
        _require(expr, isinstance(fact, th.THIROwnedRecord) and fact.type == expr.result_type
                 and fact.readonly is False, "missing or invalid full-expression storage")
        value = self.record_value(expr, fact.type, temporary=True, call=not isinstance(expr, th.THIRCtorCall))
        self.active_temporaries.append(expr)
        return _Field(fact, PTemporaryRecordPlace(expr, expr.loc, self.shape(expr.result_type, storage=True), value))

    def full_expression(self, expr: th.THIRExpr | th.THIRPrint, *, discard: bool = False,
                        expected: TpyType | None = None,
                        check: Callable[[], object] | None = None) -> PFullExpression:
        """Check `expr` as one full expression (`check`, else a scalar
        value), recording whether it owns temporaries. The plan is staged
        for the statement that owns `expr`."""
        assert self.active_temporaries is None
        self.active_temporaries = []
        try:
            with self.argument_order(expr):
                if check is not None:
                    body = check()
                elif discard and (isinstance(expr, th.THIRCtorCall) or th.record_rvalue_storage(expr) is not None):
                    body = self.temporary(expr).place
                elif discard and _user_call(expr) and isinstance(expr.result_type, VoidType):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    body = PCallStatement(expr.loc, expr.result_type, self.call(expr))
                elif (discard and isinstance(expr, th.THIRMethodCall) and expr.stub_callee is not None
                      and isinstance(expr.result_type, VoidType)):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    body = PCallStatement(expr.loc, expr.result_type, self.method_call(expr))
                else:
                    body = self.expr(expr, expected)
            return PFullExpression(expr.loc, body, bool(self.active_temporaries))
        finally:
            self.active_temporaries = None

    def expr(self, expr: th.THIRExpr, expected: TpyType | None = None) -> object:
        """Check a value expression; `expected` is the leaf type its context
        converts it to, which only an unresolved number literal adopts."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        _require(expr, storage_leaf(typ), "unsupported expression type")
        _require(expr, self.types.get(expr, typ) == typ, "literal adopts two types")
        self.types[expr] = typ
        writing = False
        match expr:
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                value = converted_literal(typ, expr.value)
                _require(expr, value is not None, "unsupported literal value")
                plan: object = PConstant(expr.loc, typ, value)
            case th.THIRCharLiteral():
                _plain(expr, {"value"})
                _require(expr, leaf_constant(typ, expr.value), "unsupported literal value")
                plan = PConstant(expr.loc, typ, expr.value)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                plan = PCallValue(expr.loc, typ, self.call(expr))
                writing = self.call_writes(expr)
            case th.THIRNarrowedRead():
                _require(expr, self.inline_union(expr) == typ, "union scalar extraction required")
                extraction = expr.union_extraction
                plan = PRead(expr.loc, typ, PUnionPayloadPlace(extraction.source, extraction.alternative))
            case th.THIRIsinstance():
                _plain(expr, {"variant_cpp", "member_cpps", "union_test"})
                fact = expr.union_test
                _require(expr, typ == BOOL and isinstance(fact, th.THIRUnionTest)
                         and self.unions.get(fact.source) == fact.layout
                         and bool(fact.alternatives) and len(set(fact.alternatives)) == len(fact.alternatives)
                         and all(type(i) is int and 0 <= i < len(fact.layout.elements)
                                 for i in fact.alternatives), "missing or invalid union test")
                plan = PIsAlternative(expr.loc, BOOL, fact.source, fact.alternatives, False)
            case th.THIRModuleVar():
                _plain(expr, {"cpp", "global_binding"})
                self.global_binding(expr)
                plan = PRead(expr.loc, typ, _global_place(expr))
            case th.THIRName() if expr.global_binding is not None:
                _plain(expr, {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
                plan = PRead(expr.loc, typ, _global_place(expr))
            case th.THIRName():
                if expr.name in self.optionals:
                    _require(expr, self.optional_name(expr, extract=True).payload == typ,
                             "optional scalar extraction required")
                    self.writes[expr] = False
                    return PRead(expr.loc, typ, PNamePlace(expr.name, True))
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.name in self.bindings, "non-local name")
                _require(expr, self.bindings[expr.name] == typ, "name type mismatch")
                plan = PRead(expr.loc, typ, PNamePlace(expr.name))
            case th.THIRFieldAccess():
                plan = PRead(expr.loc, typ, self.field(expr).place)
                # A temporary receiver's evaluation may write (a call's
                # arguments); a place receiver evaluates nothing.
                writing = self.writes.get(expr.receiver, False)
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                element = self.element_access(expr)
                member = element.member
                _require(expr, member.cls is MIRClass.LEAF and member.type == typ,
                         "element read type mismatch")
                writing = self.writes[expr.index]
                plan = PRead(expr.loc, typ, element)
            case th.THIRMethodCall() if expr.stub_callee is not None:
                plan = PCallValue(expr.loc, typ, self.method_call(expr))
                writing = self.call_writes(expr)
            case th.THIRSubscript() if expr.tuple_index is None:
                _plain(expr, {"receiver", "index", "bounds_safe"})
                _require(expr, expr.certified_op, "uncertified element read")
                receiver = self.lent_value(expr.receiver)
                _require(expr, owned_leaf(self.lent_type(expr.receiver, receiver)), "element read needs an owned leaf")
                # An element read of an owned leaf may raise (an index out of range).
                plan = POperation(expr.loc, typ, "getitem", (receiver, self.value(expr.index)))
                writing = self.writes[expr.index]
            case th.THIRSubscript():
                found = self.projection(expr)
                _require(expr, found.fact == typ, "scalar tuple projection required")
                plan = PRead(expr.loc, typ, found.place)
            case th.THIRIsNone():
                if expr.union_monostate:
                    _plain(expr, {"operand", "negate", "union_monostate"})
                    _require(expr, isinstance(expr.operand, th.THIRName) and typ == BOOL
                             and type(expr.negate) is bool, "unsupported union None test")
                    layout = self.union_name(expr.operand)
                    _require(expr, None in layout.elements, "union has no absence alternative")
                    self.writes[expr] = False
                    return PIsAlternative(expr.loc, BOOL, expr.operand.name, (layout.elements.index(None),), expr.negate)
                _plain(expr, {"operand", "negate", "value_repr"})
                if isinstance(expr.operand, th.THIRFieldAccess):
                    # An Optional record field tested in place: `std::optional` storage.
                    whole = self.field(expr.operand)
                    _require(expr, typ == BOOL and type(expr.negate) is bool
                             and isinstance(whole.fact, th.THIROptionalLayout) and expr.value_repr is True,
                             "unsupported optional field test")
                    self.writes[expr] = False
                    return PIsPresent(expr.loc, BOOL, whole.place, expr.negate)
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and isinstance(expr.operand, th.THIRName), "unsupported optional test")
                layout = self.optional_name(expr.operand, extract=False)
                _require(expr, expr.value_repr is (not isinstance(layout.payload, th.THIRBorrowedRecord)),
                         "optional test representation mismatch")
                plan = PIsPresent(expr.loc, BOOL, PNamePlace(expr.operand.name), expr.negate)
            case th.THIRCoerce() if expr.certified_conversion:
                _plain(expr, {"expr", "coercion_name", "wrap"})
                plan = PConvert(expr.loc, typ, expr.coercion_name, self.value(expr.expr))
                writing = self.writes[expr.expr]
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS
                         and isinstance(expr.expr, th.THIRLiteral), "unsupported coercion")
                _require(expr, self.expr(expr.expr, typ).type == typ, "unsupported literal coercion")
                # The literal renders directly in the slot's type.
                plan = PConstant(expr.loc, typ, expr.expr.value)
            case th.THIRWalrus():
                _require(expr, expr.name not in self.range_counters, "range target write needs separate induction")
                _plain(expr, {"name", "cpp_name", "value", "global_binding"})
                if expr.global_binding is not None:
                    self.global_binding(expr, write=True)
                else:
                    _require(expr, self.bindings.get(expr.name) == typ, "walrus needs existing scalar local")
                before = len(self.active_temporaries or ())
                value = self.expr(expr.value, typ)
                _require(expr, value.type == typ, "walrus type mismatch")
                _require(expr, expr.global_binding is None or len(self.active_temporaries or ()) == before,
                         "temporary global assignment")
                writing = True
                plan = PWalrus(expr.loc, typ, value, _global_place(expr) if expr.global_binding is not None
                               else PNamePlace(expr.name))
            case th.THIRUnaryNot():
                _plain(expr, {"operand"})
                _require(expr, typ == BOOL, "not requires bool")
                operand = self.expr(expr.operand)
                _require(expr, operand.type == BOOL, "not requires bool")
                writing = self.writes[expr.operand]
                plan = PNot(expr.loc, BOOL, operand)
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                if expr.op in ("&&", "||"):
                    left, right = self.expr(expr.left), self.expr(expr.right)
                    _require(expr, typ == BOOL and left.type == BOOL and right.type == BOOL and expr.resolved is None,
                             "unsupported boolean operation")
                    plan = PLogical(expr.loc, BOOL, expr.op, left, self.anchored(expr.right, right))
                else:
                    _require(expr, expr.certified_op, "uncertified binary operation")
                    left, right = self.promotion(expr, *self.operands(expr))
                    if ((self.writes[expr.left] and not _literal(expr.right))
                            or (self.writes[expr.right] and not _literal(expr.left))):
                        raise MIRUnsupported(expr, "order-sensitive eager operands")
                    _require(expr, expr.op not in th.COMPARISON_OPS
                             or typ == BOOL and certified_primitive_comparison(left.type, right.type),
                             "comparison operand type mismatch")
                    plan = (PCompare(expr.loc, BOOL, expr.op, left, right) if expr.op in th.COMPARISON_OPS
                            else POperation(expr.loc, typ, expr.op, (left, right)))
                writing = self.writes[expr.left] or self.writes[expr.right]
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                plan = POperation(expr.loc, typ, expr.resolved.method.name, (self.value(expr.operand),))
                writing = self.writes[expr.operand]
            case th.THIRValueSelect():
                _plain(expr, {"lhs", "rhs", "op", "lhs_temp_cpp"})
                _require(expr, expr.op in ("&&", "||") and typ == BOOL, "unsupported value select")
                before = len(self.active_temporaries or ())
                lhs = self.expr(expr.lhs)
                _require(expr, lhs.type == BOOL, "unsupported value select")
                # A hoisted LHS has its own full expression and may extend a subobject's lifetime.
                _require(expr, expr.lhs_temp_cpp is None or len(self.active_temporaries or ()) == before,
                         "temporary in hoisted select operand")
                rhs = self.expr(expr.rhs)
                _require(expr, rhs.type == BOOL,
                         "unsupported value select")
                # A pure bool select never needs a representation-changing temp.
                _require(expr, expr.lhs_temp_cpp in (None, "auto&&"), "unsupported select temporary")
                writing = self.writes[expr.lhs] or self.writes[expr.rhs]
                plan = PLogical(expr.loc, BOOL, expr.op, lhs, self.anchored(expr.rhs, rhs))
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                cond = self.expr(expr.cond)
                _require(expr, cond.type == BOOL, "condition requires bool")
                then = self.expr(expr.then, typ)
                _require(expr, then.type == typ and (orelse := self.expr(expr.orelse, typ)).type == typ,
                         "conditional arm type mismatch")
                writing = any(self.writes[e] for e in (expr.cond, expr.then, expr.orelse))
                plan = PConditional(expr.loc, typ, cond, self.anchored(expr.then, then), self.anchored(expr.orelse, orelse))
            case _:
                raise MIRUnsupported(expr, "unsupported expression")
        self.writes[expr] = writing
        return plan

    def lent_type(self, expr: th.THIRExpr, plan: object) -> TpyType:
        """The owned leaf a lent operand (`lent_value`) reads: a view's own leaf, else its type."""
        return view_owned_leaf(expr.result_type) if view_leaf(expr.result_type) else plan.type

    def promotion(self, expr: th.THIRBinOp, left: object, right: object) -> tuple[object, object]:
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
            _require(expr, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append((expr.left, expr.right)[side])
            self.leaf_layout(expr, target)
        operand = (expr.left, expr.right)[side]
        promoted = PPromote(operand.loc, target, promotion.name, (left, right)[side], owned)
        return (promoted, right) if side == 0 else (left, promoted)

    def operands(self, expr: th.THIRBinOp) -> tuple[object, object]:
        """Check both operands. A number literal converts into the type at
        its own position of the resolved dunder (`1.0 + i` binds the float
        receiver, not i's int32); under a derived comparison, to the other
        operand's type."""
        left, right = expr.left, expr.right
        if isinstance(left, th.THIRLiteral) and not isinstance(right, th.THIRLiteral):
            right_plan = self.value(right)
            return self.value(left, expr.operand_position_type(0) or right_plan.type), right_plan
        left_plan = self.value(left)
        position = expr.operand_position_type(1) if isinstance(right, th.THIRLiteral) else None
        return left_plan, self.value(right, position or left_plan.type)

    def stmt(self, stmt: th.THIRStmt, loops: int) -> object:
        match stmt:
            case th.THIRAssign(target=th.THIRName(name=name)) | th.THIRPtrLocalRebind(name=name):
                _require(stmt, name not in self.containers and name not in self.iteration_references,
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
                # A scalar payload is aliased in place, readonly.
                binding = self.bind(stmt.alias, member.type if reference else member,
                                    self.shape(member.type, member) if reference else self.shape(member, alias=True))
                fact = stmt.union_extraction
                return PNarrowAlias(stmt.loc, binding, PUnionPayloadPlace(fact.source, fact.alternative),
                                    binding.shape.holding is MIRHolding.ALIAS)
            case th.THIRAssign() if stmt.optional_record_assignment is not None:
                _plain(stmt, {"target", "value", "optional_record_assignment"})
                _require(stmt, isinstance(stmt.target, th.THIRName), "optional record assignment needs local")
                _plain(stmt.target, {"name"})
                fact = stmt.optional_record_assignment
                name = stmt.target.name
                _require(stmt, self.optional_record_storage.get(name) == fact
                         and self.bindings.get(name) == stmt.target.result_type == fact.type,
                         "optional record backing mismatch")
                value = self.record_value(stmt.value, fact.type)
                _require(stmt, self.records[fact.type].layout.movable, "optional backing needs movable record")
                return POptionalRecordAssign(stmt.loc, name, value)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.union_layout is not None:
                _require(stmt, isinstance(stmt, th.THIRAssign) and isinstance(stmt.target, th.THIRName),
                         "unsupported union reseat")
                _plain(stmt, {"target", "value", "union_layout", "union_literal"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name in self.unions and name not in self.parameters
                         and stmt.union_layout == self.unions[name], "union destination mismatch")
                return PUnionAssign(stmt.loc, name,
                                    self.union_source(stmt.value, stmt.union_layout, stmt.union_literal),
                                    self.bound(name).scalar_wrapper)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.optional_layout is not None:
                return self.optional_assignment(stmt)
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
                typ = self.bindings[stmt.target.name]
                _require(stmt, stmt.target.name not in self.parameters, "owned container parameter reseat")
                return PAssign(stmt.loc, PNamePlace(stmt.target.name), self.full_expression(
                    stmt.value, check=lambda: self.container_value(stmt.value, typ)), True)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.container_view_access(stmt.target.name) is not None):
                # Reseating a Span holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                checked = self.full_expression(stmt.value,
                                               check=lambda: self.span_value(stmt.value, self.bindings[name]))
                readonly = checked.body.readonly
                _require(stmt, not readonly or self.container_view_access(name), "alias increases access")
                return PAssign(stmt.loc, PNamePlace(name), checked, False)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and self.view_bound(stmt.target.name)):
                # Reseating a view holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                return PAssign(stmt.loc, PNamePlace(name), self.full_expression(
                    stmt.value, check=lambda: self.view_value(stmt.value, self.bindings[name])), False)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName)
                                     and owned_value_type(stmt.target.result_type) is not None):
                _plain(stmt, {"target", "value"})
                target = stmt.target
                if target.global_binding is not None:
                    self.global_binding(target, write=True)
                _plain(target, {"name", "is_last_use", "is_movable"})
                _require(stmt, self.owned_leaf_bound(target.name) is True
                         and self.bindings.get(target.name) == target.result_type,
                         "owned-leaf assignment needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.owned_value(stmt.value, sink=True))
                _require(stmt, value.type == target.result_type, "assignment type mismatch")
                # Replaces the buffer in place.
                return PAssign(stmt.loc, PNamePlace(target.name), value, True)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                     and owned_value_type(stmt.target.result_type) is not None):
                # Replaces the field's buffer in place: a write event on the field place.
                _plain(stmt, {"target", "value"})
                found = self.field(stmt.target, write=True)
                value = self.full_expression(stmt.value, check=lambda: self.owned_value(stmt.value, sink=True))
                _require(stmt, value.type == found.fact, "assignment type mismatch")
                return PAssign(stmt.loc, found.place, value, True)
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
                if isinstance(stmt.target, th.THIRName) and stmt.target.name in self.tuples:
                    _require(stmt, stmt.target.name not in self.parameters, "tuple parameter reseat")
                    _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                    _require(stmt, stmt.target.result_type == self.bindings[stmt.target.name],
                             "tuple destination type mismatch")
                    value = self.tuple_expr(stmt.value)
                    self.tuple_compatible(stmt, value.layout, self.tuples[stmt.target.name])
                    return PAssign(stmt.loc, PNamePlace(stmt.target.name), PFullExpression(value.loc, value, False),
                                   False)
                _require(stmt, isinstance(stmt.target, (th.THIRName, th.THIRFieldAccess)),
                         "assignment needs local or field target")
                _require(stmt, not isinstance(stmt.target, th.THIRName) or not self.payload_alias(stmt.target.name),
                         "write through scalar payload alias")
                if isinstance(stmt.target, th.THIRFieldAccess):
                    target_type, place = self.field(stmt.target, write=True)
                else:
                    read = self.expr(stmt.target)
                    target_type, place = read.type, read.place
                value = self.full_expression(stmt.value, expected=target_type)
                _require(stmt, not (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None
                                   and value.needs_region), "temporary global assignment")
                _require(stmt, target_type == value.type, "assignment type mismatch")
                return PScalarAssign(stmt.loc, place, value)
            case th.THIRExprStmt():
                _plain(stmt, {"expr", "void_cast"})
                return PExpressionStatement(stmt.loc, self.full_expression(stmt.expr, discard=True))
            case th.THIRReturn():
                return self.return_(stmt)
            case th.THIRForRange():
                return self.range_loop(stmt, loops)
            case th.THIRForEach():
                return self.native_loop(stmt, loops)
            case th.THIRIf():
                _plain(stmt, {"condition", "then_body", "else_body", "else_is_nested", "hoist_decls",
                              "hoisted_bindings"})
                hoists = self.hoists(stmt)
                condition = self.full_expression(stmt.condition)
                _require(stmt, condition.type == BOOL, "condition requires bool")
                then, orelse = (self.scoped(arm, loops) for arm in (stmt.then_body, stmt.else_body))
                # An elif chain flattens under a temporary plan (`if_chain`):
                # the else body is the If continuing it.
                elif_ = len(if_chain(stmt)) > 1
                return PIf(stmt.loc, condition, then, orelse, hoists, elif_, self.statement_temporaries(stmt),
                           self.temp_scope(stmt, "then"),
                           self.temp_scope(stmt, "else") if stmt.else_body and not elif_ else None,
                           self.temp_scope(stmt, "condition"))
            case th.THIRWhile():
                _plain(stmt, {"condition", "body", "orelse", "hoist_decls", "hoisted_bindings"})
                hoists = self.hoists(stmt)
                condition = self.full_expression(stmt.condition)
                _require(stmt, condition.type == BOOL, "condition requires bool")
                body = self.scoped(stmt.body, loops + 1)
                orelse = self.scoped(stmt.orelse, loops)
                # A condition with temporaries is evaluated per iteration, in a
                # temporary plan scope of its own the body shares.
                iteration = self.temp_scope(stmt, "iteration")
                return PWhile(stmt.loc, condition, body, orelse, hoists, self.statement_temporaries(stmt), iteration,
                              self.temp_scope(stmt, "loop"), self.temp_scope(stmt, "else") if stmt.orelse else None)
            case th.THIRBreak() | th.THIRContinue():
                _plain(stmt, set())
                _require(stmt, loops > 0, "loop control outside loop")
                return PJump(stmt.loc, isinstance(stmt, th.THIRContinue))
            case th.THIRParamCopy():
                # The body's owned copy of a borrowed parameter it reassigns; reads see the copy.
                _plain(stmt, {"name", "cpp_type", "init_cpp"})
                _require(stmt, any(s is stmt for s in self.fn.body) and stmt.name in self.parameters
                         and self.owned_leaf_bound(stmt.name) is False, "param copy needs a borrowed owned-leaf parameter")
                typ = self.bindings[stmt.name]
                binding = self.bind(stmt.name, typ, self.shape(typ, storage=True))
                return PParamCopy(stmt.loc, binding, type_def_of(typ).copy_may_raise)
            case th.THIRStrAppend() if stmt.target_expr is not None:
                # A field append: THIR carries the field lvalue, which the emit prefers to `target`.
                _plain(stmt, {"target", "value", "target_expr"})
                _require(stmt, isinstance(stmt.target_expr, th.THIRFieldAccess), "append needs a field place")
                found = self.field(stmt.target_expr, write=True)
                _require(stmt, primitive_owned_leaf(found.fact), "append needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
                # Python reads the field before the value runs; C++ appends to
                # whatever the value left there.
                _require(stmt, not self.writes[stmt.value], "order-sensitive eager operands")
                return PAppend(stmt.loc, found.place, stmt.target_expr.result_type, value)
            case th.THIRStrAppend():
                _plain(stmt, {"target", "value"})
                _require(stmt, self.owned_leaf_bound(stmt.target) is True and primitive_owned_leaf(self.bindings[stmt.target]),
                         "append needs owned storage")
                value = self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
                return PAppend(stmt.loc, PNamePlace(stmt.target), self.scope[stmt.target].plan.shape.type, value)
            case th.THIRPrint():
                _plain(stmt, {"args", "sep_value", "end_value"})
                _require(stmt, all(v is None or isinstance(v, str) for v in (stmt.sep_value, stmt.end_value)),
                         "print separator needs a literal")
                # The print call is one full expression: its arguments' temporaries
                # (a call's owned-leaf result, a built operand) live until it returns.
                return PExpressionStatement(stmt.loc, self.full_expression(
                    stmt, check=lambda: self.print_arguments(stmt)))
        raise MIRUnsupported(stmt, "unsupported statement")

    def optional_assignment(self, stmt: th.THIRAssign | th.THIRPtrLocalRebind) -> POptionalAssign:
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
        _require(stmt, name in self.optionals and name not in self.parameters
                 and stmt.optional_layout == self.optionals[name], "optional destination mismatch")
        if isinstance(stmt, th.THIRAssign):
            member = stmt.optional_layout.payload
            typ = member.type if isinstance(member, th.THIRBorrowedRecord) else member
            actual = unwrap_readonly(unwrap_ref_type(stmt.target.result_type))
            _require(stmt, actual in (typ, self.bindings[name]) or isinstance(actual, NoneType),
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
            backing = (PRecordBacking(stmt.value.loc, self.shape(member.type, storage=True),
                                      self.shape(member.type, member)) if own else None)
            return POptionalAssign(stmt.loc, name, value, backing, not own, self.bound(name).scalar_wrapper)
        _require(stmt, not isinstance(stmt, th.THIRAssign) or stmt.slot_cpp is None,
                 "optional slot without replacement storage")
        return POptionalAssign(stmt.loc, name, self.optional_source(stmt.value, stmt.optional_layout), None, False,
                               self.bound(name).scalar_wrapper)

    def return_(self, stmt: th.THIRReturn) -> object:
        _plain(stmt, {"value"})
        if stmt.value is None:
            _require(stmt, isinstance(self.fn.return_type, VoidType), "missing return value")
            return PReturnValue(stmt.loc, None)
        result = self.result
        if result is not None and view_leaf(result.type):
            # The result holder outlives the full expression, whose
            # temporaries a returned view must not reach.
            return PReturnHolder(stmt.loc, self.shape(result.type, _view_holder_fact(result.type)),
                                 self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, result.type)))
        if result is not None and _region_view(result.type):
            value = self.full_expression(stmt.value, check=lambda: self.span_value(stmt.value, result.type))
            _require(stmt, not value.body.readonly or result.readonly, "return increases access")
            return PReturnHolder(stmt.loc, self.container_view_shape(result.type, result.readonly), value)
        if self.container_result is not None:
            # A container place returned by reference: a borrow of it.
            _require(stmt, self.container_receiver(stmt.value), "container return needs a place")
            place = self.container_place(stmt.value)
            _require(stmt, place.type == self.container_result.type
                     and (not place.readonly or self.container_result.readonly), "return increases access")
            return PReturnBorrowed(stmt.loc, place, None)
        if result is not None:
            return PReturnBorrowed(stmt.loc, self.borrowed_expression(stmt.value, result), stmt)
        if (typ := self.owned_container_result) is not None:
            return POwnedReturn(stmt.loc, self.full_expression(stmt.value, check=lambda: self.owned_container_source(
                stmt.value, typ)), self.shape(typ, storage=True, members=self.members[typ]), False, stmt.loc)
        if (typ := self.owned_record_result) is not None:
            return POwnedReturn(stmt.loc, self.full_expression(stmt.value, check=lambda: self.owned_record_source(
                stmt.value, typ)), self.shape(typ, storage=True), True, stmt.loc)
        if (owned := owned_value_type(self.fn.return_type)) is not None:
            value = self.full_expression(stmt.value, check=lambda: self.owned_leaf_source(stmt.value))
            _require(stmt, value.type == owned, "return type mismatch")
            return POwnedReturn(stmt.loc, value, self.shape(owned, storage=True), False, value.loc)
        value = self.full_expression(stmt.value, expected=self.fn.return_type)
        _require(stmt, value.type == self.fn.return_type, "return type mismatch")
        return PReturnValue(stmt.loc, value)

    def owned_record_source(self, expr: th.THIRExpr, typ: NominalType) -> object:
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
            _require(expr, not self.references[expr.name].readonly, "readonly local returned")
            _require(expr, self.references[expr.name].type == typ, "return type mismatch")
            self.writes[expr] = False
            return PMoveOut(expr.name, typ)
        return self.record_value(expr, typ, call=True)

    def owned_leaf_source(self, expr: th.THIRExpr) -> object:
        """What an owned-leaf result takes: the body's own storage of a
        binding moved out (C++ moves it), or a copy or a build."""
        plan = self.owned_value(expr, sink=True)
        if (isinstance(plan, POwnedPlace) and isinstance(plan.place, PNamePlace)
                and self.bound(plan.place.name).is_storage):
            return PMoveOut(plan.place.name, plan.type)
        return plan

    def owned_container_source(self, expr: th.THIRExpr, typ: NominalType) -> object:
        """What an `Own[...]` container result takes: the body's own
        container storage moved out, or a literal built for it."""
        if isinstance(expr, th.THIRName) and expr.global_binding is None and self.owned_container_bound(expr.name):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, self.bindings[expr.name] == typ and expr.form in (th.Form.BORROW, th.Form.STORAGE),
                     "return type mismatch")
            return PMoveOut(expr.name, typ)
        return self.container_value(expr, typ)

    def element_write(self, stmt: th.THIRSetItem) -> PExpressionStatement:
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
            return PExpressionStatement(stmt.loc, self.full_expression(
                stmt.value, check=lambda: self.setitem_call(stmt, callee)))

        def check() -> PElementStore:
            element = self.element_access(target)
            member = element.member
            _require(stmt, not element.container.readonly, "readonly element store")
            match member.cls:
                case MIRClass.LEAF:
                    value = self.expr(stmt.value, member.type)
                    _require(stmt, value.type == member.type, "assignment type mismatch")
                case MIRClass.OWNED_LEAF:
                    value = self.owned_value(stmt.value, sink=True)
                    _require(stmt, value.type == member.type, "assignment type mismatch")
                case _:
                    value = self.record_value(stmt.value, member.type, call=True)
            self.argument_order_rule(stmt, (target.index, stmt.value))
            return PElementStore(member.type, element, value)
        return PExpressionStatement(stmt.loc, self.full_expression(stmt.value, check=check))

    def setitem_call(self, stmt: th.THIRSetItem, callee: th.THIRStubCallee) -> PSetItemCall:
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
        plans: list[object] = [place]
        receiver = summary.parameters[0]
        _require(stmt, receiver.type == place.type and not place.readonly, "readonly element store")
        for arg, binding in zip((target.index, stmt.value), summary.parameters[1:]):
            _require(arg, not binding.protocol, "unsupported element call parameter")
            plans.append(self.argument(arg, binding))
        self.argument_order_rule(stmt, (target.index, stmt.value))
        self.calls[stmt] = summary
        # No call site: `xs[i] = v` is no call expression a diagnostic could find its arguments in.
        return PSetItemCall(callee.signature.return_type, PCall(None, summary, tuple(plans), False))

    def print_arguments(self, stmt: th.THIRPrint) -> PPrint:
        plans: list[object] = []
        for arg in stmt.args:
            _plain(arg, {"expr", "print_form"})
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS | _OWNED_PRINT_FORMS,
                     "print argument needs a scalar leaf")
            plan = self.value(arg.expr)
            plans.append(plan)
            # A view prints the leaf it views.
            typ = through_view(plan.type)
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS and primitive_leaf(typ)
                     or arg.print_form in _OWNED_PRINT_FORMS and primitive_owned_leaf(typ),
                     "print argument needs a scalar leaf")
        # The arguments of the variadic print call evaluate in an unspecified order.
        self.argument_order_rule(stmt, tuple(arg.expr for arg in stmt.args))
        return PPrint(VoidType(), tuple(plans))

    def range_loop(self, stmt: th.THIRForRange, loops: int) -> PRangeLoop:
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
        bounds: list[object] = []
        for bound, literal in ((stmt.start, stmt.start_is_literal), (stmt.stop, stmt.stop_is_literal)):
            _require(stmt, type(literal) is bool and literal is (bound is None or _literal(bound)),
                     "range bound capture disagrees with expression")
            plan = None
            if bound is not None:
                admitted = _literal(bound) or isinstance(bound, (th.THIRName, th.THIRFieldAccess, th.THIRSubscript))
                plan = self.expr(bound, INT32) if admitted else None
                _require(bound, plan is not None and plan.type == INT32, "unsupported range bound")
            bounds.append(plan)
        _require(stmt, stmt.var not in self.range_counters, "nested range target changes outer induction")
        _require(stmt, self.bindings.get(stmt.var) == INT32 if stmt.hoist_loop_var else stmt.var not in self.bindings,
                 "range target residence mismatch")
        counters, scope = self.range_counters.copy(), self.scope.copy()
        binding = None if stmt.hoist_loop_var else self.bind(stmt.var, INT32, self.shape(INT32))
        # An unwritten, unhoisted target is the counter itself; any other is
        # a copy of it per iteration.
        direct = not stmt.hoist_loop_var and not stmt.target_written
        if direct:
            self.range_counters.add(stmt.var)
        body = self.scoped(stmt.body, loops + 1)
        self.range_counters, self.scope = counters, scope
        orelse = self.scoped(stmt.orelse, loops)
        return PRangeLoop(stmt.loc, hoists, bounds[0], bounds[1], 1 if stmt.step_kind == "plus_one" else -1,
                          binding, stmt.var, direct, body, orelse, self.temp_scope(stmt, "counter"),
                          self.temp_scope(stmt, "loop"), self.temp_scope(stmt, "else") if stmt.orelse else None)

    def native_loop(self, stmt: th.THIRForEach, loops: int) -> PForEach:
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
        source = fact.source
        _require(stmt, isinstance(source, th.THIRNativeContainer), "missing or invalid native iteration facts")
        # A cursor binds the source's declared element, one at a time.
        _require(stmt, binds_cursor(place.type), "missing or invalid native iteration facts")
        element = place.element
        _require(stmt, isinstance(source, th.THIRNativeContainer) and source.type == place.type
                 and source.readonly == place.readonly
                 and source.element == (th.THIRBorrowedRecord(element.type, element.readonly)
                                        if element.cls is MIRClass.RECORD else element.type),
                 "native iteration source disagrees with binding")
        reference = (th.THIRBorrowedRecord(element.type, element.readonly)
                     if element.cls is MIRClass.RECORD else None)
        # An owned-leaf element is read through a view of it, or a readonly borrow.
        view = element.cls is MIRClass.OWNED_LEAF and view_leaf(stmt.elem_type)
        typ = stmt.elem_type if view else element.type
        _require(stmt, (view_compatible(stmt.elem_type, element.type) if view
                        else unwrap_readonly(stmt.elem_type) == typ)
                 and stmt.binding.loop_binding in (LoopBinding.VALUE, LoopBinding.ASSIGN, LoopBinding.REFERENCE,
                                                   LoopBinding.CONST_REFERENCE)
                 and (reference is None or not stmt.hoist_loop_var and stmt.const_loop_var == reference.readonly)
                 and (element.cls is not MIRClass.OWNED_LEAF or not stmt.hoist_loop_var and (
                     view or stmt.binding.loop_binding is LoopBinding.CONST_REFERENCE)),
                 "unsupported native target binding")
        # The iterator walks the slot holding the place: a binding's own, or a holder of a field or a call result.
        holder = place.holder if place.holder is not None else self.scope[place.root.name].plan.shape
        iterator = self.shape(holder.type, container=th.THIRNativeContainer(holder.type, source.element, holder.readonly),
                              iterator=True)
        hoists = self.hoists(stmt)
        _require(stmt, stmt.var not in self.range_counters and stmt.var not in self.iteration_references,
                 "nested loop replaces outer iteration binding")
        _require(stmt, self.bindings.get(stmt.var) == typ if stmt.hoist_loop_var else stmt.var not in self.bindings,
                 "native target residence mismatch")
        targets, scope = self.iteration_references.copy(), self.scope.copy()
        if reference or view or element.cls is MIRClass.OWNED_LEAF:
            self.iteration_references.add(stmt.var)
        binding = None
        if not stmt.hoist_loop_var:
            binding = self.bind(stmt.var, typ, self.shape(reference.type, reference) if reference
                                else self.shape(stmt.elem_type, _view_holder_fact(stmt.elem_type)) if view
                                else self.shape(element.type, th.THIRBorrowedRecord(element.type, True))
                                if element.cls is MIRClass.OWNED_LEAF else self.shape(stmt.elem_type))
        body = self.scoped(stmt.body, loops + 1)
        self.iteration_references = targets
        self.scope = scope
        orelse = self.scoped(stmt.orelse, loops)
        return PForEach(stmt.loc, hoists, place, iterator, binding, stmt.var, body, orelse,
                        self.temp_scope(stmt, "loop"), self.temp_scope(stmt, "else") if stmt.orelse else None)

    def hoists(self, stmt: th.THIRIf | th.THIRWhile | th.THIRForRange | th.THIRForEach) -> tuple[PHoist, ...]:
        facts = stmt.hoisted_bindings
        _require(stmt, all(isinstance(f, th.THIRHoistedBinding) for f in facts)
                 and tuple(f.name for f in facts) == tuple(decl.name for decl in stmt.hoist_decls),
                 "missing or inconsistent hoisted binding facts")
        hoisted: list[PHoist] = []
        for fact in facts:
            _require(stmt, fact.name not in self.bindings and fact.initially_assigned is False
                     and fact.placement is th.THIRStoragePlacement.SCOPE, "invalid hoisted binding placement or availability")
            layouts = (fact.borrowed_record, fact.optional_layout, fact.tuple_layout, fact.union_layout,
                       fact.optional_record_storage)
            _require(stmt, sum(f is not None for f in layouts) <= 1, "conflicting hoisted binding facts")
            if fact.optional_record_storage is not None:
                reference = fact.optional_record_storage
                self.reference(stmt, reference, fact.type)
                self.wrapped_record(stmt, reference.type)
                _require(stmt, not reference.readonly, "optional record backing must be mutable")
                definition = self.definitions.get(stmt, reference.type)
                _require(stmt, definition.layout.movable, "optional backing needs movable record")
                self.records[reference.type] = definition
                typ = reference.type
                plan = (self.shape(typ, reference), self.shape(typ, storage=True), reference)
            elif fact.borrowed_record is not None:
                self.reference(stmt, fact.borrowed_record, fact.type)
                typ = fact.borrowed_record.type
                plan = (self.shape(fact.type, fact.borrowed_record), None, None)
            elif fact.optional_layout is not None:
                self.optional_layout(stmt, fact.optional_layout, fact.type)
                typ = fact.type
                plan = (self.shape(fact.type, optional_layout=fact.optional_layout), None, fact.optional_layout)
            elif fact.union_layout is not None:
                self.union_layout(stmt, fact.union_layout, fact.type)
                _require(stmt, all(m is None or storage_leaf(m) for m in fact.union_layout.elements),
                         "unsupported hoisted union storage")
                typ = fact.type
                plan = (self.shape(fact.type, union_layout=fact.union_layout), None, fact.union_layout)
            elif fact.tuple_layout is not None:
                self.tuple_layout(stmt, fact.tuple_layout, fact.type)
                typ = fact.type
                plan = (self.shape(fact.type, tuple_layout=fact.tuple_layout), None, fact.tuple_layout)
            else:
                _require(stmt, storage_leaf(fact.type), "unsupported hoisted value")
                typ = fact.type
                plan = (self.shape(fact.type), None, None)
            wrapper = (fact.union_layout is not None or fact.optional_layout is not None
                       and storage_leaf(fact.optional_layout.payload))
            default = fact.physical_default
            if wrapper:
                first = fact.union_layout.elements[0] if fact.union_layout is not None else None
                expected = None if first is None else zero_value_of(first)
                _require(stmt, (first is None or expected is not None)
                         and isinstance(default, th.THIRWrapperDefault)
                         and type(default.alternative) is int and default.alternative == 0
                         and type(default.value) is type(expected) and default.value == expected,
                         "missing or inconsistent physical wrapper default")
            else:
                _require(stmt, default is None, "physical default on non-wrapper hoist")
            hoisted.append(PHoist(self.bind(fact.name, typ, *plan),
                                  (default.alternative, default.value) if default is not None else None))
        return tuple(hoisted)

    def scoped(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> tuple[object, ...]:
        scope, tuple_roots, fixed_owned = self.scope.copy(), self.tuple_roots.copy(), self.fixed_owned.copy()
        plans = self.declarations(stmts, loops)
        self.scope, self.tuple_roots = scope, tuple_roots
        self.fixed_owned = fixed_owned & self.fixed_owned
        return plans


@dataclass(frozen=True, eq=False)
class MIRLoweredStorage:
    function: MIRFunction
    # Captured when the emit pass allocates storage, never reconstructed from IDs.
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


def _lower(body: MIRBodyId, coverage: _Coverage) -> MIRLoweredStorage | MIRNotCovered:
    """Check the body into its plan, then build it; a refusal of either is the body's verdict."""
    try:
        plan = coverage.check()
        emitter = PlanEmitter(body, plan)
        try:
            function = emitter.build()
        except MIRRepeatedInitializationError as failure:
            raise MIRUnsupported(failure, str(failure)) from failure
        except (MIRPresenceError, MIRDefiniteAssignmentError, MIRFallthroughError) as failure:
            raise MIRUnsupported(coverage.fn, str(failure)) from failure
        return MIRLoweredStorage(function, MappingProxyType(emitter.backing_places),
                                 MappingProxyType(emitter.borrow_operations))
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))


def lower_function_storage(fn: th.THIRFunction, body: MIRBodyId, *,
                           definitions: MIRDefinitions | None = None,
                           summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                           ) -> MIRLoweredStorage | MIRNotCovered:
    """The ordinary lowering pass, retaining its actual backing correspondence."""
    # Resumable, generic and closure bodies never reach this pass: the
    # collector and the workspace refuse them before scheduling a lowering.
    return _lower(body, _Coverage(fn, definitions if definitions is not None else MIRDefinitions(), summaries))


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
        receiver=th.THIRBorrowedRecord(initialization.layout.type, False), temp_plan=ctor.temp_plan)
    return _lower(body, _Coverage(fn, definitions, summaries, initialization, ctor))
