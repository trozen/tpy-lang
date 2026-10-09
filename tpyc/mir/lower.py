"""All-or-nothing lowering of scalar and record-storage THIR operations."""

from dataclasses import dataclass, field
from contextlib import contextmanager
from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType

from ..identity_map import IdentityMap, IdentitySet
from ..codegen_cpp.forms import LoopBinding, loop_binding_kind
from ..type_def_registry import is_array
from ..parse import ResultForm, RebindStorage, SourceLocation
from ..thir import nodes as th
from ..thir.temp_plan import if_chain, validate_plan
from ..thir.scalar_leaves import (
    binds_cursor, container_view, converted_literal, declared_members, holds_loan, leaf_constant, leaf_global,
    native_container_subject, native_container_type, owned_constant, owned_leaf,
    owned_value_type, primitive_leaf, primitive_owned_leaf, readonly_elements, record_type, storage_leaf,
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
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBranch, MIRStorageInit, MIRStatement,
    MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRGoto, MIRFunction, MIRNot, MIRNotCovered, MIRReceiverInit, MIRGlobalId,
    MIRMemberInit, MIRMemberInitMode, MIRMemberInits, member_init_operands,
    MIRDeref, MIRField, MIRFieldId, MIRPlace, MIRPoint, MIRRead, MIRReturn, MIRRvalue,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRTerminator, MIRValueKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRRecordStorageInit, MIRRecordStorageKind,
    MIRRecordWrite, MIRRecordWriteMode, MIRPayloadWrite, MIRPayloadWriteMode,
    MIRRecordLayout, MIRRegion, MIRRegionId, function_body_kind,
    MIRRangeAdvance, MIROp, MIRPrint,
    MIRContainerElements, MIRContainerLayout, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout, MIRTupleInitialization,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRUnionLayout, MIRUnionPayload, MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract,
    statement_target,
)
from .coverage import (
    MIRUnsupported, container_view_holder, literal_type, owned_container, plain as _plain, require as _require,
    scalar_param, scalar_wrapper,
)
from .definitions import (
    MIRComposedConstruct, MIRConstructorDefinition, MIRContainerDefinition, MIRDefinitions, MIRFieldInitializer,
    MIRHeldLayout, MIROwnedLeafDefinition, constructor_initialization, layout_copy_may_raise, owned_parameter,
    owned_record_parameter, record_parameter, with_access,
)
from .call_contract import (
    BORROWING_PASSINGS, OWNING_PASSINGS, MIRCallSummary, MIRParameterBinding, MIRSummaryResult,
    MIRSummaryState, binds_at, borrowed_result_of, bound_result, container_result, owned_record_result,
    path_hops, stub_protocol_argument, stub_summary, summary_problem, transfer_ends, view_result,
)
from .validate import (MIRDefiniteAssignmentError, MIRPresenceError, MIRRepeatedInitializationError,
                       body_may_raise, reaches_element, statement_reads, successors,
                       validate_function)


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


def _call_arguments(expr: th.THIRCall | th.THIRMethodCall) -> tuple[th.THIRExpr, ...]:
    """The arguments a call binds in parameter order: a method's receiver is parameter 0."""
    return (expr.receiver, *expr.args) if isinstance(expr, th.THIRMethodCall) else expr.args


def _copy_may_raise(records: Mapping[NominalType, MIRConstructorDefinition | MIROwnedLeafDefinition],
                    typ: NominalType) -> bool:
    """Whether copying a record of `typ` the body models may raise
    (`layout_copy_may_raise` over the body's layouts)."""
    return layout_copy_may_raise(records[typ].layout, lambda t: records[t].layout if t in records else None)


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


@dataclass(frozen=True)
class _ContainerPlace:
    """A checked container place: its type, its layout under the place's
    access, and that access."""
    type: NominalType
    layout: MIRContainerLayout
    readonly: bool


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
    def __init__(self, fn: th.THIRFunction, definitions: MIRDefinitions,
                 summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None) -> None:
        self.fn = fn
        self.bindings: dict[str, TpyType] = {}
        self.references: dict[str, th.THIRBorrowedRecord] = {}
        self.parameters = {p.name for p in fn.params}
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()
        self.definitions = definitions
        self.records: dict[NominalType, MIRConstructorDefinition | MIROwnedLeafDefinition | MIRHeldLayout] = {}
        # Owned-leaf bindings: True for storage the body owns (a local, a
        # by-value parameter, a parameter's copy), False for a borrow of
        # storage outside it (a const-ref or view parameter).
        self.owned_bindings: dict[str, bool] = {}
        # Names bound to a view holder (a view local or parameter).
        self.views: set[str] = set()
        # The borrowed result the body returns: a record or a view.
        self.result: th.THIRBorrowedRecord | None = None
        # Locals holding their own record storage, and those never reseated
        # (a reseat may retarget the holder at another backing). The first
        # tells a reseated local (refused as such) from a parameter, which
        # is in neither.
        self.owned_records: set[str] = set()
        self.fixed_owned: set[str] = set()
        self.optional_record_storage: dict[str, th.THIRBorrowedRecord] = {}
        self.tuples: dict[str, th.THIRTupleLayout] = {}
        self.tuple_roots: dict[str, str] = {}
        self.body_declarations = {id(stmt) for stmt in fn.body if isinstance(stmt, th.THIRVarDecl)}
        self.tuple_exprs: IdentityMap[th.THIRExpr, th.THIRTupleLayout] = IdentityMap()
        self.optionals: dict[str, th.THIROptionalLayout] = {}
        self.unions: dict[str, th.THIRUnionLayout] = {}
        self.payload_aliases: set[str] = set()
        self.globals: dict[MIRGlobalId, th.THIRGlobalBinding] = {}
        self.range_counters: set[str] = set()
        self.containers: dict[str, th.THIRNativeContainer] = {}
        # Names bound to owned container storage (a literal's local, an
        # `Own[...]` parameter), and to Span holders with their access.
        self.owned_containers: set[str] = set()
        self.container_views: dict[str, bool] = {}
        # The mutable-access layout of every container type the body reaches.
        self.layouts: dict[NominalType, MIRContainerLayout] = {}
        # The checked place of each container subscript, slice and method receiver.
        self.element_places: IdentityMap[th.THIRExpr, _ContainerPlace] = IdentityMap()
        # Each Span local's access: readonly when its element or its source is.
        self.span_holders: IdentityMap[th.THIRVarDecl, bool] = IdentityMap()
        # Declarations binding a record element in place (`p = ps[0]`).
        self.element_bindings: IdentityMap[th.THIRVarDecl, th.THIRBorrowedRecord] = IdentityMap()
        # A container result: owned storage moved out, or a borrow of a container place.
        self.container_result: th.THIRBorrowedRecord | None = None
        self.owned_container_result: NominalType | None = None
        # An `Own[R]` record result: the body's own record storage moved out.
        self.owned_record_result: NominalType | None = None
        self.iteration_references: set[str] = set()
        self.full_expressions: IdentitySet[th.THIRExpr] = IdentitySet()
        # Expressions whose storage the current full expression owns: record
        # constructor temporaries and owned-leaf temporaries.
        self.active_temporaries: list[th.THIRExpr] | None = None
        self.summaries = summaries if summaries is not None else {}
        self.calls: IdentityMap[th.THIRCall | th.THIRMethodCall, MIRCallSummary] = IdentityMap()
        # Each user call's borrowed result, at the access its receiver is bound at.
        self.call_results: IdentityMap[th.THIRCall | th.THIRMethodCall, th.THIRBorrowedRecord | None] = IdentityMap()
        self.stub_summaries: dict[th.THIRStubIdentity, MIRCallSummary] = {}
        self.borrowed_bindings: IdentityMap[th.THIRStmt, th.THIRBorrowedRecord] = IdentityMap()
        self.argument_temporaries: IdentityMap[th.THIRArgTemp, th.THIRBorrowedRecord] = IdentityMap()
        self.select_temporaries: IdentityMap[th.THIRSlotEmplace, th.THIRBorrowedRecord] = IdentityMap()
        # Each checked record source (`record_source`): the record it names and its access.
        self.record_sources: IdentityMap[th.THIRExpr, th.THIRBorrowedRecord] = IdentityMap()
        # The leaf type each checked expression has in MIR; a number literal's
        # comes from its context, not its node.
        self.types: IdentityMap[th.THIRExpr, TpyType] = IdentityMap()

    def call(self, expr: th.THIRCall | th.THIRMethodCall, *, temporary: bool = False) -> None:
        """A call through its callee's summary, parameter by parameter over
        `_call_arguments`: a free-function call, or a resolved user method
        call rendered as the plain member call (a pointer receiver spells
        its access `->`). `temporary`: the result is materialized in
        storage of its full expression."""
        if isinstance(expr, th.THIRCall) and expr.stub_callee is not None:
            self.stub_call(expr)
            return
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
            # A container returned by reference: a place of the layout its type derives.
            self.container_layout(expr, result.type)
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
        for index, (arg, binding) in enumerate(zip(_call_arguments(expr), summary.parameters)):
            ref = binding.borrowed_record
            if ref is None:
                self.argument(arg, binding)
            else:
                if isinstance(arg, th.THIRArgTemp):
                    # A temporary receiver binds readonly (argument_temporary
                    # requires it), so its follows-receiver result is readonly
                    # too: narrower than the mutable clone C++ may pick, never wider.
                    self.argument_temporary(arg, ref)
                    receiver_readonly = receiver_readonly or index == 0
                    continue
                if isinstance(arg, th.THIRFieldAccess):
                    # A member place, lent for the call through a holder of it.
                    actual = self.record_source(arg)
                else:
                    _require(arg, isinstance(arg, (th.THIRName, th.THIRSelf))
                             and arg.form is th.Form.BORROW, "call needs borrowed record name")
                    source_name = arg.name if isinstance(arg, th.THIRName) else "self"
                    _require(arg, source_name not in self.payload_aliases and source_name not in self.optionals,
                             "call needs unwrapped record binding")
                    actual = self.references[self.reference_name(arg)]
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

    def stub_call(self, expr: th.THIRCall) -> None:
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
                    self.element_places[arg] = self.container_place(arg)
                    self.writes[arg] = False
                    continue
                if owned_leaf(read):
                    self.lent_value(arg)
                    continue
                # An owned-leaf or view result may borrow what the parameter
                # binds; a scalar argument has no storage to borrow.
                _require(arg, owned is None and view_result(signature.return_type) is None,
                         "stub result may borrow a scalar argument")
            self.argument(arg, binding)
        self.argument_order_rule(expr, expr.args)
        self.calls[expr] = summary

    def method_call(self, expr: th.THIRMethodCall, *, view: bool = False) -> _ContainerPlace:
        """A call to a native container's method stub, summarized from its
        declaration (`call_contract.stub_summary`): the receiver place is
        parameter 0, lent whole; the arguments follow the stub-call rows.
        With `view`, the result is a container view of the receiver's
        region. Returns the receiver's place."""
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
        self.element_places[expr.receiver] = place
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
                    self.lent_value(arg)
                    continue
            self.argument(arg, binding)
        self.argument_order_rule(expr, expr.args)
        self.calls[expr] = summary
        return place

    def view_call(self, expr: th.THIRMethodCall) -> _ContainerPlace:
        """A container view a method stub returns (`d.keys()`): a holder of
        its receiver's elements region, readonly when the view's elements
        or the receiver are."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        place = self.method_call(expr, view=True)
        result = self.calls[expr].borrowed_result
        layout = self.container_layout(expr, result.type)
        readonly = result.readonly or place.readonly
        self.writes[expr] = False
        return _ContainerPlace(result.type, with_access(layout, readonly), readonly)

    def argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> None:
        """One argument at its binding: an owned leaf lent or copied, else
        any admitted scalar expression, evaluated into a temporary before
        the call (a scalar global is read there). Evaluation order among
        arguments is the order rule's (`argument_order_rule`)."""
        if owned_value_type(binding.type) is not None:
            self.owned_argument(arg, binding.type, binding.passing)
            return
        if view_leaf(binding.type):
            # A view passed by value hands the callee the loan it holds.
            self.view_value(arg, binding.type)
            return
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
            self.literal_record(arg, binding.type)
            return
        if native_container_type(binding.type) or container_view(binding.type):
            self.container_argument(arg, binding)
            return
        typ = arg.result_type if binding.protocol else binding.type
        _require(arg, not isinstance(arg, th.THIRName)
                 or (arg.name not in self.optionals and arg.name not in self.unions),
                 "call needs unwrapped scalar binding")
        _require(arg, self.expr(arg, typ) == typ, "call scalar argument mismatch")

    def container_argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> None:
        """A container argument: a place lent whole for the call (a Span
        holder lends the region it views), or owned container storage moved
        into the temporary the callee takes over."""
        if binding.passing in OWNING_PASSINGS:
            _require(arg, isinstance(arg, th.THIRMove) and isinstance(arg.value, th.THIRName)
                     and arg.value.global_binding is None and arg.value.name in self.owned_containers,
                     "container argument needs an owned move")
            _plain(arg, {"value"})
            _plain(arg.value, {"name", "is_last_use", "is_movable"})
            _require(arg, self.bindings[arg.value.name] == binding.type, "call container argument mismatch")
            _require(arg, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append(arg)
        else:
            _require(arg, self.container_receiver(arg), "call container argument needs a place")
            place = self.container_place(arg)
            self.element_places[arg] = place
            _require(arg, place.type == binding.type and (binding.readonly or not place.readonly),
                     "call container argument mismatch")
        self.writes[arg] = False

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

    def owned_argument(self, arg: th.THIRExpr, typ: TpyType, passing: ParamPassing) -> None:
        """An owned-leaf argument: lent for the call at a borrowing passing
        (a name or literal in place, anything built a temporary of the full
        expression), else the callee's own copy, copied or built into a
        temporary the full expression owns."""
        if passing in BORROWING_PASSINGS:
            if view_leaf(arg.result_type):
                # A view of the parameter's family is lent as the view it is.
                self.view_value(arg, arg.result_type)
                _require(arg, view_compatible(arg.result_type, typ), "call owned-leaf argument mismatch")
                return
            _require(arg, self.owned_value(arg, sink=False) == typ, "call owned-leaf argument mismatch")
            return
        _require(arg, passing in OWNING_PASSINGS, "unsupported owned-leaf argument passing")
        _require(arg, self.owned_value(arg, sink=True) == typ, "call owned-leaf argument mismatch")
        _require(arg, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
        self.active_temporaries.append(arg)

    def argument_temporary(self, arg: th.THIRArgTemp, reference: th.THIRBorrowedRecord) -> None:
        _require(arg, self.fn.temp_plan is not None, "named argument needs complete temporary plan")
        placement = self.fn.temp_plan.placement(arg)
        _plain(arg, {"init", "cpp_type", "movable"})
        _require(arg, arg.form is th.Form.BORROW and (isinstance(arg.init, th.THIRCtorCall) or _user_call(arg.init))
                 and arg.result_type == reference.type and reference.readonly,
                 "named argument needs readonly record constructor")
        self.record_value(arg.init, reference.type, call=True)
        self.stable_constructor_operands(arg.init)
        definition = self.records[reference.type]
        _require(arg, not placement.optional or definition.layout.movable and arg.would_bank(),
                 "deferred argument needs movable backing")
        self.argument_temporaries[arg] = reference

    def select_temporary(self, expr: th.THIRSlotEmplace, reference: th.THIRBorrowedRecord) -> None:
        _require(expr, self.fn.temp_plan is not None, "select storage needs complete temporary plan")
        placement = self.fn.temp_plan.placement(expr)
        _plain(expr, {"value", "cpp_type"})
        _require(expr, placement.optional and placement.initialization is expr,
                 "select storage needs conditional emplacement")
        _require(expr, isinstance(expr.value, th.THIRCtorCall), "select storage needs record constructor")
        self.record_value(expr.value, reference.type)
        self.stable_constructor_operands(expr.value)
        _require(expr, self.records[reference.type].layout.movable, "select storage needs movable backing")
        self.select_temporaries[expr] = reference

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
                        and expr.name not in self.unions and expr.name not in self.payload_aliases)
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

    def check(self) -> None:
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
            self.container_layout(fn, unwrap_readonly(unwrap_ref_type(fn.return_type)))
            self.result = view_result(fn.return_type)
            _require(fn, self.result is not None, "unsupported return type")
        elif native_container_type(bare := unwrap_readonly(unwrap_ref_type(fn.return_type))):
            # A container returned by reference is borrowed like a record result.
            self.result = self.container_result = (
                bound_result(signature, receiver_readonly) if signature is not None
                and signature.result_follows_receiver else container_result(fn.return_type))
            _require(fn, self.result is not None, "unsupported return type")
            self.container_layout(fn, bare)
        elif native_container_type(bare := unwrap_own(fn.return_type)):
            # An `Own[...]` container returns the body's storage by value.
            _require(fn, return_representation(fn.return_type) is Representation.STORAGE, "unsupported return type")
            self.container_layout(fn, bare)
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
        if fn.receiver is not None:
            self.reference(fn, fn.receiver, fn.receiver.type)
            self.bindings["self"] = fn.receiver.type
            self.references["self"] = fn.receiver
            self.parameters.add("self")
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
                self.container_layout(p, bare)
                self.container_views[p.name] = _container_view_fact(bare, False).readonly
                self.bindings[p.name] = bare
            elif p.native_container is not None:
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout, p.tuple_layout)),
                         "conflicting parameter facts")
                self.container(p, p.native_container, p.type)
                self.bindings[p.name] = p.native_container.type
                if p.passing is ParamPassing.OWN:
                    # An `Own[...]` container is the body's own storage from entry.
                    _require(p, not p.native_container.readonly, "readonly owned container parameter")
                    self.owned_containers.add(p.name)
                else:
                    _require(p, p.passing in (ParamPassing.CONST_REF, ParamPassing.MUT_REF),
                             "unsupported container parameter passing")
                    self.containers[p.name] = p.native_container
            elif p.tuple_layout is not None:
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout)),
                         "conflicting parameter facts")
                self.tuple_layout(p, p.tuple_layout, p.type)
                self.tuples[p.name] = p.tuple_layout
                self.bindings[p.name] = unwrap_ref_type(unwrap_readonly(unwrap_ref_type(p.type)))
            elif p.union_layout is not None:
                _require(p, p.borrowed_record is None and p.optional_layout is None, "conflicting parameter facts")
                self.union_layout(p, p.union_layout, p.type)
                self.unions[p.name] = p.union_layout
                self.bindings[p.name] = p.union_layout.type
            elif p.optional_layout is not None:
                _require(p, p.borrowed_record is None, "conflicting parameter facts")
                self.optional_layout(p, p.optional_layout, p.type)
                self.optionals[p.name] = p.optional_layout
                self.bindings[p.name] = unwrap_readonly(unwrap_ref_type(p.type))
            elif p.borrowed_record is not None:
                self.reference(p, p.borrowed_record, p.type)
                self.references[p.name] = p.borrowed_record
                self.bindings[p.name] = p.borrowed_record.type
            elif (owned := owned_value_type(p.type)) is not None:
                _require(p, p.passing in BORROWING_PASSINGS | OWNING_PASSINGS,
                         "unsupported owned-leaf parameter passing")
                self.bindings[p.name] = owned
                self.owned_bindings[p.name] = p.passing in OWNING_PASSINGS
                self.leaf_layout(p, owned)
            elif view_leaf(p.type):
                # A view passed by value is the caller's loan, held by the parameter.
                _require(p, p.passing is ParamPassing.VALUE, "unsupported view parameter passing")
                self.bindings[p.name] = p.type
                self.views.add(p.name)
            elif (record := owned_record_parameter(p)) is not None:
                # A record handed over at OWN binds the caller's temporary
                # (`R&&`), which ends with the caller's full expression. It
                # is modeled as the body's own storage ending at body exit:
                # an earlier end, so never a missed conflict. Reached through
                # a holder as an owned record local is; never reseated. The
                # loans it stores are the caller's, seeded at entry.
                self.record_layout(p, record)
                self.bindings[p.name] = record
                self.references[p.name] = th.THIRBorrowedRecord(record, False)
                self.owned_records.add(p.name)
                self.fixed_owned.add(p.name)
            else:
                _require(p, not container_view(unwrap_readonly(unwrap_ref_type(p.type))), "view of container storage")
                self.unpublished_container(p, p.type)
                _require(fn, scalar_param(p), "unsupported parameter type")
                self.bindings[p.name] = p.type
        self.declarations(fn.body, 0)
        if self.argument_temporaries or self.select_temporaries:
            _require(fn, len(self.argument_temporaries) + len(self.select_temporaries) == len(fn.temp_plan.placements),
                     "temporary plan contains unsupported record storage")

    def declarations(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> None:
        for stmt in stmts:
            if isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl)):
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
                    self.owned_container_local(stmt)
                    continue
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
                    self.element_places[stmt.init] = place
                    _require(stmt, stmt.form is th.Form.BORROW and stmt.is_const == fact.readonly
                             and place.type == fact.type and (fact.readonly or not place.readonly)
                             and (not isinstance(stmt.init, th.THIRName) or stmt.init.name in self.containers)
                             and stmt.name not in self.fn.layout.reassigned_locals,
                             "container alias needs fixed matching borrowed source")
                    self.containers[stmt.name] = stmt.native_container
                    self.bindings[stmt.name] = stmt.native_container.type
                    continue
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
                    self.union_source(stmt.init, stmt.union_layout, stmt.union_literal)
                    self.unions[stmt.name] = stmt.union_layout
                    self.bindings[stmt.name] = stmt.union_layout.type
                    continue
                if stmt.optional_layout is not None:
                    allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "optional_layout", "storage_placement"}
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
                            self.record_value(stmt.init, fact.type)
                        else:
                            _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE and stmt.init is None,
                                     "optional backing storage")
                    _plain(stmt, allowed)
                    self.optional_layout(stmt, stmt.optional_layout, stmt.resolved_type)
                    if stmt.owned_storage is None:
                        self.optional_source(stmt.init, stmt.optional_layout)
                    self.optionals[stmt.name] = stmt.optional_layout
                    self.bindings[stmt.name] = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
                    continue
                if isinstance(stmt, th.THIRVarDecl) and stmt.tuple_storage_alias is not None:
                    self.tuple_alias(stmt)
                    continue
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
                    self.tuple_compatible(stmt, self.tuple_expr(stmt.init, allow_owned=owning), stmt.tuple_layout)
                    self.tuples[stmt.name] = stmt.tuple_layout
                    self.bindings[stmt.name] = stmt.resolved_type
                    if owning and id(stmt) in self.body_declarations:
                        self.tuple_roots[stmt.name] = stmt.name
                    continue
                if stmt.owned_storage is not None:
                    self.owned(stmt)
                    continue
                if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                    self.borrow_binding(stmt, declaration=True)
                    continue
                if ((_user_call(stmt.init) or isinstance(stmt.init, th.THIRIfExpr))
                        and (isinstance(stmt, th.THIRPtrLocalDecl) or stmt.form is th.Form.BORROW)):
                    self.borrowed_binding(stmt, declaration=True)
                    continue
                if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.BORROW
                        and isinstance(stmt.init, th.THIRSubscript) and stmt.init.tuple_index is None
                        and self.container_receiver(stmt.init.receiver)):
                    self.element_binding(stmt)
                    continue
                _require(stmt, isinstance(stmt, th.THIRVarDecl), "missing alias binding")
                # A VALUE-form leaf is held by value whichever way its C++
                # declaration is spelled (an enum local names its type).
                _plain(stmt, {"name", "resolved_type", "init", "is_const", "cpp_type"})
                if owned_leaf(stmt.resolved_type):
                    self.owned_local(stmt)
                    continue
                if view_leaf(stmt.resolved_type):
                    self.view_local(stmt)
                    continue
                if _region_view(stmt.resolved_type):
                    self.span_local(stmt)
                    continue
                _require(stmt, not container_view(unwrap_readonly(unwrap_ref_type(stmt.resolved_type))),
                         "view of container storage")
                self.unpublished_container(stmt, stmt.resolved_type)
                _require(stmt, not holds_borrowing_view(stmt.resolved_type), "view local")
                _require(stmt, stmt.form is th.Form.VALUE and storage_leaf(stmt.resolved_type),
                         "unsupported local type or form")
                _require(stmt, stmt.name not in self.bindings, "duplicate binding")
                if stmt.init is not None:
                    _require(stmt, self.full_expression(stmt.init, expected=stmt.resolved_type) == stmt.resolved_type,
                             "initializer type mismatch")
                self.bindings[stmt.name] = stmt.resolved_type
            else:
                self.stmt(stmt, loops)

    def owned_local(self, stmt: th.THIRVarDecl) -> None:
        typ = stmt.resolved_type
        _require(stmt, stmt.form in (th.Form.VALUE, th.Form.STORAGE), "unsupported local type or form")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        # A declaration without a value would default-construct storage no write event names.
        _require(stmt, stmt.init is not None, "owned-leaf declaration needs an initializer")
        _require(stmt, self.full_expression(stmt.init, check=lambda: self.owned_value(stmt.init, sink=True)) == typ,
                 "initializer type mismatch")
        self.leaf_layout(stmt, typ)
        self.bindings[stmt.name] = typ
        self.owned_bindings[stmt.name] = True

    def owned_container_local(self, stmt: th.THIRVarDecl) -> None:
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
        self.full_expression(stmt.init, check=lambda: self.container_value(stmt.init, fact.type))
        self.owned_containers.add(stmt.name)
        self.bindings[stmt.name] = fact.type

    def container_value(self, expr: th.THIRExpr, typ: NominalType) -> TpyType:
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
        layout = self.layouts[typ]
        members = (layout.element,) if layout.value is None else (layout.element, layout.value)
        # Literal construction: the one place the compiler knows a container's length.
        _require(expr, len(expr.values) == (0 if layout.value is None else len(expr.elements))
                 and (not is_array(typ) or len(expr.elements) == typ.type_args[1]),
                 "container initializer type mismatch")
        operands = (expr.elements if layout.value is None
                    else tuple(e for pair in zip(expr.elements, expr.values) for e in pair))
        for index, operand in enumerate(operands):
            member = members[index % len(members)]
            match member.kind:
                case MIRValueKind.SCALAR:
                    _require(operand, self.expr(operand, member.type) == member.type,
                             "container element type mismatch")
                case MIRValueKind.OWNED:
                    _require(operand, self.owned_value(operand, sink=False) == member.type,
                             "container element type mismatch")
                case _:
                    self.literal_record(operand, member.type)
        self.argument_order_rule(expr, operands)
        self.writes[expr] = any(self.writes.get(o, False) for o in operands)
        return typ

    def literal_record(self, expr: th.THIRExpr, typ: NominalType) -> None:
        """A record element of a container literal: constructed, or handed
        over by a call's owned result, into a temporary of the full
        expression, which the literal moves from, or copied through the
        holder a name is."""
        if isinstance(expr, th.THIRCtorCall) or _user_call(expr):
            _require(expr, self.active_temporaries is not None, "temporary needs full-expression boundary")
            self.record_value(expr, typ, call=True)
            self.active_temporaries.append(expr)
            return
        _require(expr, isinstance(expr, th.THIRCopy), "unsupported container element")
        self.record_value(expr, typ)

    def element_binding(self, stmt: th.THIRVarDecl) -> None:
        """A local binding one record element in place (`p = ps[0]`): a
        borrow of the container's element region, never retargeted."""
        _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "cpp_local_representation"})
        _require(stmt, stmt.name not in self.fn.layout.reassigned_locals, "element binding is reassigned")
        _require(stmt, stmt.name not in self.bindings, "duplicate binding")
        fact = th.THIRBorrowedRecord(unwrap_readonly(unwrap_ref_type(stmt.resolved_type)), stmt.is_const)

        def check() -> TpyType:
            member = self.element_access(stmt.init)
            _require(stmt, member.kind is MIRValueKind.BORROWED and member.type == fact.type
                     and stmt.init.form is th.Form.BORROW and (not member.readonly or fact.readonly),
                     "element binding type or access mismatch")
            return fact.type
        self.full_expression(stmt.init, check=check)
        self.element_bindings[stmt] = fact
        self.references[stmt.name] = fact
        self.bindings[stmt.name] = fact.type

    def span_local(self, stmt: th.THIRVarDecl) -> None:
        """A container view local: a holder of the region its source views, readonly
        when its element is or when that region is."""
        typ = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
        _require(stmt, stmt.form is th.Form.VALUE and stmt.init is not None, "unsupported local type or form")
        self.container_layout(stmt, typ)
        readonly = self.full_expression(stmt.init, check=lambda: self.span_value(stmt.init, typ))
        self.container_views[stmt.name] = self.span_holders[stmt] = _container_view_fact(typ, readonly).readonly
        self.bindings[stmt.name] = typ

    def span_value(self, expr: th.THIRExpr, holder: NominalType) -> bool:
        """Check an expression a container view holder takes; returns whether the
        region it views is readonly. Every Span-producing operation
        transfers referents and never copies: a Span is aliased, an
        unstepped slice borrows its receiver's whole element region after
        its bounds are read (Python slices clamp, so it does not raise)."""
        element = self.container_layout(expr, holder).element.type
        match expr:
            case th.THIRName() if expr.global_binding is None and expr.name in self.container_views:
                _plain(expr, {"name", "is_last_use", "is_movable"})
                source = self.bindings[expr.name]
                _require(expr, self.layouts[source].element.type == element, "view source type mismatch")
                readonly = self.container_views[expr.name]
                writing = False
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, _region_view(expr.result_type) and _region_view(expr.expr.result_type),
                         "unsupported view source")
                readonly = self.span_value(expr.expr, unwrap_readonly(unwrap_ref_type(expr.expr.result_type)))
                writing = self.writes[expr.expr]
            case th.THIRStrSlice():
                _plain(expr, {"receiver", "cpp_template", "lower", "upper", "step", "stepped", "index"})
                _require(expr, not expr.stepped, "stepped slice")
                _require(expr, expr.step is None and expr.index is None, "unsupported view source")
                bounds = [b for b in (expr.lower, expr.upper) if b is not None]
                for bound in bounds:
                    _require(bound, int_traits_of(self.expr(bound)) is not None,
                             "slice bound needs a fixed-width int")
                place = self.container_place(expr.receiver)
                self.element_places[expr] = place
                _require(expr, place.layout.value is None and place.layout.element.type == element,
                         "view source type mismatch")
                readonly = place.readonly
                self.argument_order_rule(expr, tuple(bounds))
                writing = any(self.writes[b] for b in bounds)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        self.writes[expr] = writing
        return readonly or readonly_elements(holder)

    def view_local(self, stmt: th.THIRVarDecl) -> None:
        typ = stmt.resolved_type
        _require(stmt, stmt.form is th.Form.VALUE, "unsupported local type or form")
        # A declaration without a value holds an empty view no source names.
        _require(stmt, stmt.init is not None, "view declaration needs an initializer")
        self.full_expression(stmt.init, check=lambda: self.view_value(stmt.init, typ))
        self.bindings[stmt.name] = typ
        self.views.add(stmt.name)

    def lent_value(self, expr: th.THIRExpr) -> TpyType:
        """Check an operand an operation reads in place: a view, or an owned
        leaf borrowed in place or built into a temporary of the full
        expression. Returns the owned leaf the operation reads."""
        if view_leaf(expr.result_type):
            self.view_value(expr, expr.result_type)
            return view_owned_leaf(expr.result_type)
        return self.owned_value(expr, sink=False)

    def view_value(self, expr: th.THIRExpr, holder: TpyType) -> TpyType:
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
            _require(expr, view_compatible(holder, source), "view source type mismatch")
            return holder
        _require(expr, self.types.get(expr, typ) == typ and view_compatible(holder, typ), "view source type mismatch")
        writing = False
        match expr:
            case th.THIRName() if expr.global_binding is None and expr.name in self.views:
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
            case th.THIRCoerce():
                # A view-target coercion renders a view of its source in place.
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.form is th.Form.BORROW, "unsupported view source")
                self.view_value(expr.expr, typ)
                writing = self.writes[expr.expr]
            case th.THIRStrSlice():
                _plain(expr, {"receiver", "cpp_template", "lower", "upper", "step", "stepped", "index"})
                _require(expr, not expr.stepped, "stepped slice")
                _require(expr, expr.form is th.Form.BORROW and expr.step is None and expr.index is None,
                         "unsupported view source")
                bounds = [b for b in (expr.lower, expr.upper) if b is not None]
                for bound in bounds:
                    _require(bound, int_traits_of(self.expr(bound)) is not None,
                             "slice bound needs a fixed-width int")
                self.view_value(expr.receiver, typ)
                # The bounds and the receiver evaluate in an unspecified order.
                operands = (*bounds, expr.receiver)
                for operand in operands:
                    if self.writes[operand]:
                        _require(expr, all(other is operand or _literal(other) or isinstance(other, th.THIRName)
                                           for other in operands), "order-sensitive eager operands")
                writing = any(self.writes[o] for o in operands)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                self.call(expr)
                result = self.calls[expr].borrowed_result
                _require(expr, result is not None and result.type == typ, "call view result mismatch")
                writing = self.call_writes(expr)
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                member = self.element_access(expr)
                _require(expr, member.kind is MIRValueKind.OWNED and expr.form is th.Form.BORROW
                         and view_compatible(holder, member.type), "view source type mismatch")
                writing = self.writes[expr.index]
            case th.THIRFieldAccess():
                # The loan a view member stores, read whole.
                _require(expr, self.field(expr) == typ, "view source type mismatch")
                writing = self.writes.get(expr.receiver, False)
            case _:
                raise MIRUnsupported(expr, "unsupported view source")
        self.types[expr] = typ
        self.writes[expr] = writing
        return typ

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
        layout = self.tuple_expr(stmt.init, allow_owned=True)
        _require(stmt, fact.layout == layout and layout.owns_records, "tuple alias layout or access mismatch")
        self.bindings[stmt.name] = stmt.resolved_type
        self.tuples[stmt.name] = layout
        self.tuple_roots[stmt.name] = self.tuple_roots[fact.source]

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
            # An inline member is storage of exactly its declared type.
            fact = arg.field_identity
            bare = unwrap_readonly(fact.type) if isinstance(fact, th.THIRFieldIdentity) else None
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
                bare = unwrap_readonly(member.type)
                if owned_leaf(member.type):
                    self.leaf_layout(self.fn, member.type)
                elif record_type(bare) and bare not in self.records:
                    self.records[bare] = self.definitions.get(self.fn, bare)
                    pending.append(self.records[bare])

    def value(self, expr: th.THIRExpr, expected: TpyType | None = None) -> TpyType:
        """Check an operand: an owned leaf read through a borrow, else a
        scalar value (`expr`)."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        if view_leaf(typ):
            return self.view_value(expr, typ)
        if owned_leaf(typ):
            return self.owned_value(expr, sink=False, expected=expected)
        return self.expr(expr, expected)

    def owned_value(self, expr: th.THIRExpr, *, sink: bool, expected: TpyType | None = None) -> TpyType:
        """Check an owned-leaf expression. At a sink its value lands in the
        destination's storage; as an operand a name or a literal is borrowed
        in place, and anything else is built into a temporary of the
        enclosing full expression, which that expression's region ends."""
        typ = literal_type(expr, expected) if isinstance(expr, th.THIRLiteral) else expr.result_type
        _require(expr, owned_leaf(typ), "unsupported owned-leaf expression type")
        _require(expr, self.types.get(expr, typ) == typ, "literal adopts two types")
        fresh = True
        writing = False
        match expr:
            case th.THIRModuleVar():
                _plain(expr, {"cpp", "global_binding"})
                self.global_binding(expr)
                fresh = False
            case th.THIRName() if expr.global_binding is not None:
                _plain(expr, {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
                fresh = False
            case th.THIRName():
                # The binding, not the name's form, says whether it is storage or a borrow.
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.name in self.owned_bindings and self.bindings.get(expr.name) == typ,
                         "non-local name")
                fresh = False
            case th.THIRStrLiteral():
                _plain(expr, {"value"})
                _require(expr, expr.form is th.Form.VALUE and owned_constant(typ, expr.value),
                         "unsupported literal value")
                fresh = False
            case th.THIRBytesLiteral():
                _plain(expr, {"value"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
                # A borrow-form literal is a static span; a storage-form one builds a buffer.
                _require(expr, expr.form is th.Form.STORAGE or expr.form is th.Form.BORROW and not sink,
                         "borrowed bytes literal at an owning sink")
                fresh = expr.form is th.Form.STORAGE
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                _require(expr, owned_constant(typ, expr.value), "unsupported literal value")
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                _require(expr, expr.op not in ("&&", "||") and expr.certified_op, "uncertified binary operation")
                self.promotion(expr, *self.operands(expr))
                if ((self.writes[expr.left] and not _literal(expr.right))
                        or (self.writes[expr.right] and not _literal(expr.left))):
                    raise MIRUnsupported(expr, "order-sensitive eager operands")
                writing = self.writes[expr.left] or self.writes[expr.right]
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                self.value(expr.operand)
                writing = self.writes[expr.operand]
            case th.THIRCoerce() if expr.owned_passthrough:
                # The source's own storage, read as its family's owned type: lent
                # at a borrowing sink, copied at an owning one.
                _plain(expr, {"expr", "coercion_name"})
                self.lent_value(expr.expr)
                fresh = False
                writing = self.writes[expr.expr]
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name", "wrap"})
                if (refusal := expr.conversion_refusal) is not None:
                    raise MIRUnsupported(expr, refusal)
                self.value(expr.expr)
                writing = self.writes[expr.expr]
            case th.THIRFormConvert() if view_leaf(expr.value.result_type):
                # A view at an owning sink: a copy of what it views.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW,
                         "unsupported form conversion")
                self.view_value(expr.value, expr.value.result_type)
                _require(expr, view_compatible(expr.value.result_type, typ), "form conversion type mismatch")
                writing = self.writes[expr.value]
            case th.THIRFormConvert():
                # Only the copy of a borrowed read into owned storage: moves,
                # materialized views and generic conversions are not leaf values.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and expr.value.form is th.Form.BORROW
                         and isinstance(expr.value, (th.THIRName, th.THIRModuleVar)), "unsupported form conversion")
                _require(expr, self.owned_value(expr.value, sink=False) == typ, "form conversion type mismatch")
            case th.THIRStrSlice() if expr.stepped:
                raise MIRUnsupported(expr, "stepped slice")
            case th.THIRMove():
                raise MIRUnsupported(expr, "owned-leaf move")
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                # The callee returns an owned leaf by value: fresh storage for the
                # caller, unless a stub's result may borrow what the call lends --
                # a borrow of those arguments, copied at an owning sink.
                self.call(expr)
                summary = self.calls[expr]
                result = summary.borrowed_result
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
                         and (result is None or result.type == typ and result.readonly),
                         "call owned result mismatch")
                fresh = result is None
                writing = self.call_writes(expr)
            case th.THIRFieldAccess():
                # A field place: borrowed in place as an operand, copied at a sink.
                _require(expr, self.field(expr) == typ, "owned-leaf field type mismatch")
                fresh = False
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                # An element place, like a field: borrowed in place as an
                # operand, copied at a sink.
                member = self.element_access(expr)
                _require(expr, member.kind is MIRValueKind.OWNED and member.type == typ,
                         "owned-leaf element type mismatch")
                fresh = False
                writing = self.writes[expr.index]
            case th.THIRMethodCall() if expr.stub_callee is not None:
                self.method_call(expr)
                summary = self.calls[expr]
                _require(expr, owned_value_type(summary.callee.signature.return_type) == typ
                         and summary.borrowed_result is None, "call owned result mismatch")
                writing = self.call_writes(expr)
            case _:
                raise MIRUnsupported(expr, "unsupported owned-leaf expression")
        if fresh and not sink:
            _require(expr, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append(expr)
        self.leaf_layout(expr, typ)
        self.types[expr] = typ
        self.writes[expr] = writing
        return typ

    def record_value(self, expr: th.THIRExpr, typ: NominalType, *, temporary: bool = False,
                     call: bool = False) -> None:
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
                for arg, param in zip(expr.args, params):
                    if owned_parameter(param):
                        # Lent to a member that copies it, or handed over for one to move from.
                        self.owned_argument(arg, owned_value_type(param.type), param.passing)
                    elif (record := owned_record_parameter(param)) is not None:
                        # Handed over: a temporary of the full expression the member moves from.
                        self.record_operand(arg, record)
                    elif record_parameter(param):
                        # Lent: a holder the member copies through, a temporary bound to it.
                        self.lent_record_operand(arg, param.borrowed_record.type)
                    elif view_leaf(param.type):
                        # A view parameter takes the loan its argument holds.
                        self.view_value(arg, param.type)
                    else:
                        _require(arg, self.expr(arg, param.type) == param.type, "constructor argument type")
                    _require(arg, not self.writes[arg], "effectful constructor argument")
            case th.THIRCopy():
                _plain(expr, {"value", "cpp_type"})
                _require(expr, self.record_source(expr.value).type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable and expr.form is th.Form.STORAGE,
                         "record is not copyable or copy form")
            case th.THIRMove():
                _plain(expr, {"value"})
                source = self.reference_name(expr.value)
                _require(expr, self.references[source].type == typ, "record source type mismatch")
                _require(expr, definition.layout.movable and source in self.fixed_owned
                         and not self.references[source].readonly
                         and expr.form is expr.value.form, "move needs fixed movable owned local")
            case (th.THIRName() | th.THIRSelf()) if expr.form is th.Form.BORROW:
                # A borrowed name read into record storage: C++ copies what
                # it names (sema warned about the inline copy).
                _require(expr, self.record_source(expr).type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
            case th.THIRFieldAccess() if expr.form is th.Form.STORAGE:
                # A member read into record storage: a copy of that member.
                _require(expr, self.record_source(expr).type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
            case th.THIRFormConvert():
                # A local's storage converted into storage: a copy of it.
                _plain(expr, {"value", "is_const"})
                _require(expr, expr.form is th.Form.STORAGE and isinstance(expr.value, (th.THIRName, th.THIRSelf))
                         and expr.value.form is th.Form.BORROW, "unsupported form conversion")
                _require(expr, self.record_source(expr.value).type == typ, "record source type mismatch")
                _require(expr, definition.layout.copyable, "record is not copyable or copy form")
            case th.THIRCall() | th.THIRMethodCall() if call and _user_call(expr):
                self.call(expr, temporary=temporary)
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
            case _:
                raise MIRUnsupported(expr, "unsupported record initializer")
        self.writes[expr] = writing

    def record_operand(self, arg: th.THIRExpr, typ: NominalType) -> None:
        """A record argument built into storage of its full expression: a
        construct, a call handing over its owned result, a copy, or a fixed
        owned local moved out."""
        _require(arg, isinstance(arg, (th.THIRCtorCall, th.THIRCopy, th.THIRMove)) or _user_call(arg),
                 "constructor argument type")
        # The temporary stores the loans its construct, the callee's
        # transfers or the copied or moved object name.
        _require(arg, self.active_temporaries is not None, "temporary needs full-expression boundary")
        self.record_value(arg, typ, call=True)
        self.active_temporaries.append(arg)

    def lent_record_operand(self, arg: th.THIRExpr, typ: NominalType) -> None:
        """A record argument lent to a constructor parameter: a record
        source in place (a name, or a member read through one), or a
        temporary bound for the call (never a move: binding the lent
        parameter moves nothing)."""
        if isinstance(arg, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)):
            _require(arg, self.record_source(arg).type == typ, "constructor argument type")
            self.writes[arg] = False
            return
        _require(arg, not isinstance(arg, th.THIRMove), "constructor argument type")
        self.record_operand(arg, typ)

    def owned(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> None:
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

        def initializer() -> TpyType:
            self.record_value(stmt.init, fact.type, call=True)
            return fact.type
        # A constructor argument may need a temporary (an owned leaf built for
        # it), which the declaration's full expression owns.
        self.full_expression(stmt.init, check=initializer)
        if isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED:
            _require(stmt, self.records[fact.type].layout.movable, "hoisted backing needs movable record")
        self.bindings[stmt.name] = fact.type
        self.references[stmt.name] = fact
        self.owned_records.add(stmt.name)
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.STORAGE
                and stmt.name not in self.fn.layout.reassigned_locals):
            self.fixed_owned.add(stmt.name)

    def replacement(self, stmt: th.THIRAssign) -> None:
        _plain(stmt, {"target", "value", "rebind_storage", "slot_cpp"})
        name = self.reference_name(stmt.target)
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                 "unsupported replacement storage")
        _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE
                 or not self.references[name].readonly, "readonly in-place replacement")
        typ = self.references[name].type

        def value() -> TpyType:
            self.record_value(stmt.value, typ, call=True)
            return typ
        # Its argument temporaries end with the replacing full expression.
        self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[self.references[name].type].layout.movable,
                 "replacement needs movable record")
        # A STORAGE-form local is one sema never rebinds (a rebound local is a
        # rebind-slot pointer local), so `fixed_owned` is a whole-body fact; a
        # reseat of a member would hand a returned backing back stale.
        _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")

    def member_write(self, stmt: th.THIRAssign) -> None:
        """`ln.a = v`: an inline record member replaced whole, in place --
        the record around it keeps its identity, so the write is an event
        of the member place only. The value is what record storage takes
        (`record_value`); C++ copy- or move-assigns it."""
        _plain(stmt, {"target", "value"})
        member = self.field(stmt.target, write=True)
        _require(stmt, isinstance(member, th.THIRBorrowedRecord) and stmt.target.form is th.Form.STORAGE,
                 "record member write needs member storage")
        if holds_loan(member.type):
            # The write fills the member's entries under the object around it.
            self.held_root(stmt.target)

        def value() -> TpyType:
            self.record_value(stmt.value, member.type, call=True)
            return member.type
        # Its argument temporaries end with the replacing full expression.
        self.full_expression(stmt.value, check=value)
        _require(stmt, self.records[member.type].layout.movable, "record member replacement needs movable record")

    def view_member_write(self, stmt: th.THIRAssign) -> None:
        """`t.s = v`: a view member rebound to the loan `v` holds. The record
        keeps its identity and no storage is replaced; every object the
        place may reach stores the loan (`dependencies.store_loan`)."""
        _plain(stmt, {"target", "value"})
        typ = self.field(stmt.target, write=True)
        # A loan held by a temporary of the statement ends with it, which
        # the scope-end analysis reports.
        self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, typ))

    def reference(self, node: object, ref: th.THIRBorrowedRecord, typ: TpyType) -> None:
        _require(node, isinstance(ref, th.THIRBorrowedRecord), "invalid reference fact")
        _require(node, record_type(ref.type) and type(ref.readonly) is bool, "unsupported reference fact")
        _require(node, unwrap_readonly(unwrap_ref_type(typ)) == ref.type,
                 "reference type mismatch")

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

    def container_layout(self, node: object, typ: TpyType) -> MIRContainerLayout:
        """The mutable-access layout of a native container (or of the region a
        Span views); its record members' definitions and owned-leaf members'
        layouts come along, since places of its elements reach them."""
        bare = unwrap_readonly(unwrap_ref_type(typ))
        _require(node, isinstance(bare, NominalType) and (native_container_type(bare) or container_view(bare)),
                 "unsupported native container type")
        definition = self.definitions.get(node, bare)
        _require(node, isinstance(definition, MIRContainerDefinition), "unsupported native container type")
        for record in definition.records:
            self.records[record.layout.type] = record
        for member in (definition.layout.element, definition.layout.value):
            if member is not None and member.kind is MIRValueKind.OWNED:
                self.leaf_layout(node, member.type)
        self.layouts[bare] = definition.layout
        return definition.layout

    def unpublished_container(self, node: object, typ: TpyType) -> None:
        """A container binding THIR published no fact for: its members'
        refusal when they have one (an element holding a borrow, a nested
        container), else the missing fact."""
        bare = native_container_subject(typ)
        if isinstance(bare, NominalType) and native_container_type(bare):
            self.container_layout(node, bare)
            raise MIRUnsupported(node, "missing native container fact")

    def container(self, node: object, fact: th.THIRNativeContainer, typ: TpyType) -> MIRContainerLayout:
        """Check a container fact against its type and the layout the type
        derives; the fact's element is the member iteration yields. An
        `Own[...]` parameter publishes the fact for the container it owns."""
        bare = native_container_subject(typ)
        _require(node, isinstance(fact, th.THIRNativeContainer) and isinstance(bare, NominalType)
                 and fact.type == bare and type(fact.readonly) is bool
                 and (not isinstance(unwrap_ref_type(typ), ReadonlyType) or fact.readonly),
                 "invalid native container fact")
        _require(node, native_container_type(bare), "unsupported native container type")
        layout = self.container_layout(node, bare)
        member, expected = fact.element, layout.element
        if isinstance(member, th.THIRBorrowedRecord):
            self.reference(node, member, member.type)
            _require(node, expected.kind is MIRValueKind.BORROWED and member.type == expected.type
                     and member.readonly == fact.readonly, "unsupported native record element")
        else:
            _require(node, expected.kind is not MIRValueKind.BORROWED and member == expected.type,
                     "unsupported native scalar element")
        return layout

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
                return (expr.name in self.containers or expr.name in self.owned_containers
                        or expr.name in self.container_views)
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

    def container_place(self, expr: th.THIRExpr) -> _ContainerPlace:
        """Check a container place expression: its type, its layout under
        the access the place has, and whether that access is readonly."""
        match expr:
            case th.THIRName() if expr.name in self.containers:
                fact = self.container_name(expr)
                return _ContainerPlace(fact.type, with_access(self.layouts[fact.type], fact.readonly), fact.readonly)
            case th.THIRName() if expr.name in self.owned_containers or expr.name in self.container_views:
                _plain(expr, {"name", "is_last_use", "is_movable"})
                typ = self.bindings[expr.name]
                # An `Own[...]` parameter's name keeps the wrapper THIR gave the
                # parameter; the storage it names is the bare container.
                _require(expr, native_container_subject(expr.result_type) == typ
                         and (expr.form in (th.Form.BORROW, th.Form.VALUE)
                              or expr.form is th.Form.STORAGE and expr.name in self.owned_containers),
                         "container name type mismatch")
                readonly = self.container_views.get(expr.name, False)
                return _ContainerPlace(typ, with_access(self.layouts[typ], readonly), readonly)
            case th.THIRFieldAccess():
                fact = self.field(expr)
                _require(expr, isinstance(fact, th.THIRNativeContainer), "container field needs container storage")
                return _ContainerPlace(fact.type, with_access(self.layouts[fact.type], fact.readonly), fact.readonly)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr) and self.container_receiver(expr):
                return self.call_container(expr)
            case th.THIRMethodCall() if self.container_receiver(expr):
                return self.view_call(expr)
        raise MIRUnsupported(expr, "container source needs a place")

    def call_container(self, expr: th.THIRCall | th.THIRMethodCall) -> _ContainerPlace:
        """A container a user call returns by reference: a place of the
        callee's container (its referents are the summary's return
        origins), at the access the call's result is bound at."""
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        self.call(expr)
        result = self.call_results[expr]
        _require(expr, result is not None and native_container_type(result.type)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == result.type,
                 "call container result mismatch")
        # The builder evaluates a place's operands (an index, slice bounds)
        # before its container, so the call must not write what they read.
        _require(expr, not self.call_writes(expr), "order-sensitive eager operands")
        self.writes[expr] = False
        return _ContainerPlace(result.type, with_access(self.layouts[result.type], result.readonly), result.readonly)

    def element_index(self, expr: th.THIRSubscript, place: _ContainerPlace) -> None:
        """Check a subscript's index or key: a fixed-width int position, or
        a key of the dict's key type (an owned-leaf key is lent, a view of
        it lent as the view)."""
        index = expr.index
        _, _, keyed = declared_members(place.type)
        # A key addresses the value member, a position the element; a type
        # keyed by its only member, or whose value member no key reaches, is not modeled.
        _require(index, keyed is (place.layout.value is not None), "unsupported keyed container")
        if not keyed:
            _require(index, int_traits_of(self.expr(index)) is not None, "element index needs a fixed-width int")
            return
        key = place.layout.element.type
        if storage_leaf(key):
            _require(index, self.expr(index, key) == key, "element key type mismatch")
        else:
            _require(index, through_view(self.lent_value(index)) == key, "element key type mismatch")

    def element_access(self, expr: th.THIRSubscript) -> MIRTupleElement:
        """Check a container subscript as a whole: its place, its index, and
        that the operands cannot reorder a write. Returns the member it reaches."""
        _plain(expr, {"receiver", "index", "bounds_safe"})
        _require(expr, expr.tuple_index is None, "tuple projection of a container")
        place = self.container_place(expr.receiver)
        self.element_index(expr, place)
        _require(expr, not self.writes[expr.index] or _literal(expr.index), "order-sensitive eager operands")
        self.element_places[expr] = place
        return place.layout.subscript

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

    def record_source(self, expr: th.THIRExpr) -> th.THIRBorrowedRecord:
        """The record storage an expression names in place, at the access
        it is reached with: a name or `self` (what its holder reaches), or
        a member read through them (`ln.a`, `self.line.a`). Only a copy, a
        returned borrow or a lent argument takes a member; reseats, aliases
        and wrapper captures keep needing a name (`reference_name`)."""
        if isinstance(expr, th.THIRFieldAccess):
            _require(expr, expr.form is th.Form.STORAGE, "record member read form")
            member = self.field(expr)
            _require(expr, isinstance(member, th.THIRBorrowedRecord), "record source needs record storage")
            self.record_sources[expr] = member
            return member
        source = self.references[self.reference_name(expr)]
        self.record_sources[expr] = source
        return source

    def borrowed_expression(self, expr: th.THIRExpr, result: th.THIRBorrowedRecord) -> None:
        with self.argument_order(expr):
            if isinstance(expr, th.THIRFieldAccess):
                # A member of record storage the result reaches, borrowed in
                # place (THIR reads it in STORAGE form, as a storage borrow's
                # source); never lent with more access than its path has.
                self.reference(expr, result, expr.result_type)
                source = self.record_source(expr)
                _require(expr, source.type == result.type and (not source.readonly or result.readonly),
                         "return increases access")
                return
            self._borrowed_expression(expr, result)

    def _borrowed_expression(self, expr: th.THIRExpr, result: th.THIRBorrowedRecord) -> None:
        _require(expr, expr.form is (th.Form.VALUE if _user_call(expr) else th.Form.BORROW),
                 "unsupported borrowed expression form")
        self.reference(expr, result, expr.result_type)
        match expr:
            case th.THIRSlotEmplace():
                self.select_temporary(expr, result)
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                self.call(expr)
                actual = self.call_results[expr]
                _require(expr, actual is not None and actual.type == result.type
                         and (not actual.readonly or result.readonly), "call result increases access")
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                _require(expr, self.expr(expr.cond) == BOOL, "condition requires bool")
                self._borrowed_expression(expr.then, result)
                self._borrowed_expression(expr.orelse, result)
            case _:
                source = self.references[self.reference_name(expr)]
                _require(expr, not source.readonly or result.readonly, "return increases access")

    def borrowed_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> None:
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
        self.borrowed_expression(source, fact)
        self.borrowed_bindings[stmt] = fact
        if declaration:
            self.bindings[stmt.name] = fact.type
            self.references[stmt.name] = fact
        else:
            _require(stmt, stmt.name not in self.fixed_owned, "fixed owned local reseated")

    def borrow_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> None:
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
                reference = self.projection(source, capture=True)
            else:
                _require(source, isinstance(source, th.THIRFieldAccess) and source.form is th.Form.STORAGE,
                         "storage borrow needs record field")
                reference = self.field(source)
            _require(source, isinstance(reference, th.THIRBorrowedRecord), "storage borrow needs record")
        else:
            source_name = self.reference_name(source)
            _require(stmt, alias.source == source_name, "alias source mismatch")
            reference = self.references[source_name]
        self.reference(stmt, fact, reference.type)
        _require(stmt, not reference.readonly or fact.readonly, "alias increases access")
        if declaration:
            self.bindings[name] = fact.type
            self.references[name] = fact
        else:
            _require(stmt, self.references.get(name) == fact, "alias destination mismatch")
            _require(stmt, name not in self.fixed_owned, "fixed owned local reseated")

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
                      literal: th.THIRUnionLiteral | None = None) -> None:
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
            return
        if isinstance(expr, th.THIRName) and expr.name in self.unions:
            source = self.union_name(expr)
            _require(expr, source.type == target.type, "union copy type mismatch")
            for src, dst in zip(source.elements, target.elements):
                if src is None or dst is None:
                    _require(expr, src is dst, "union copy absence mismatch")
                else:
                    self.payload_compatible(expr, src, dst)
            return
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            _require(expr, None in target.elements, "union has no absence alternative")
            return
        if isinstance(expr, th.THIRName) and expr.name in self.references:
            source = self.references[self.reference_name(expr)]
            matches = [m for m in target.elements if isinstance(m, th.THIRBorrowedRecord) and m.type == source.type]
            _require(expr, len(matches) == 1, "union has no matching record alternative")
            self.payload_compatible(expr, source, matches[0])
        else:
            typ = self.expr(expr)
            _require(expr, typ in target.elements and not self.writes[expr], "unsupported union member construction")

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
        outer_readonly = unwrap_readonly(unwrap_ref_type(typ)) != unwrap_ref_type(typ)
        typ = unwrap_readonly(unwrap_ref_type(typ))
        _require(node, isinstance(layout, th.THIROptionalLayout)
                 and isinstance(typ, OptionalType) and not typ.force_pointer_repr,
                 "unsupported optional layout")
        member = layout.payload
        if isinstance(member, th.THIRBorrowedRecord):
            self.reference(node, member, typ.inner)
            self.wrapped_record(node, member.type)
            _require(node, (not outer_readonly and unwrap_readonly(typ.inner) == typ.inner) or member.readonly,
                     "optional layout increases access")
        else:
            _require(node, storage_leaf(member) and unwrap_readonly(typ.inner) == member,
                     "unsupported optional payload")

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

    def optional_source(self, expr: th.THIRExpr | None, target: th.THIROptionalLayout) -> None:
        if expr is None:
            return
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            if isinstance(expr.result_type, OptionalType):
                self.optional_layout(expr, target, expr.result_type)
            else:
                _require(expr, isinstance(expr.result_type, (NoneType, VoidType)),
                         "optional absence type")
        elif isinstance(expr, th.THIRName) and expr.name in self.optionals and (
                expr.optional_read is not None and not expr.optional_read.extract):
            source = self.optional_name(expr, extract=False)
            self.payload_compatible(expr, source.payload, target.payload)
        elif isinstance(target.payload, th.THIRBorrowedRecord):
            _require(expr, isinstance(expr, th.THIRName), "optional capture needs local name")
            source = self.references[self.reference_name(expr)]
            _require(expr, source.type == target.payload.type
                     and (not source.readonly or target.payload.readonly), "optional capture access mismatch")
        elif _literal(expr):
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
        else:
            _require(expr, self.expr(expr, target.payload) == target.payload and not self.writes[expr],
                     "effectful or mistyped optional payload")

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

    def tuple_expr(self, expr: th.THIRExpr, *, allow_owned: bool = False) -> th.THIRTupleLayout:
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW, th.Form.STORAGE), "unsupported tuple form")
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
                    self.record_value(element, member.type)
                elif isinstance(member, th.THIRBorrowedRecord):
                    _require(expr, isinstance(expr, th.THIRBorrowTupleLiteral), "value tuple borrows record")
                    name = self.reference_name(element)
                    source = self.references[name]
                    _require(element, source.type == member.type and (not source.readonly or member.readonly),
                             "tuple capture type or access mismatch")
                else:
                    _require(element, self.expr(element, member) == member and not self.writes[element],
                             "effectful or mistyped tuple element")
        self.tuple_layout(expr, layout, expr.result_type, allow_owned=allow_owned)
        _require(expr, expr.form is th.Form.STORAGE if layout.owns_records and isinstance(expr, th.THIRName)
                 else expr.form in (th.Form.VALUE, th.Form.BORROW), "tuple form disagrees with layout")
        self.tuple_exprs[expr] = layout
        return layout

    def projection(self, expr: th.THIRSubscript, *, capture: bool = False) -> TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord:
        _plain(expr, {"receiver", "index", "tuple_index", "elem_pointer"}
               | ({"deref"} if capture else set()))
        _require(expr, not capture or expr.deref, "tuple capture needs dereferenced element")
        layout = self.tuple_expr(expr.receiver, allow_owned=True)
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
        return member

    def field(self, expr: th.THIRFieldAccess, *, write: bool = False) -> TpyType | th.THIRBorrowedRecord:
        _plain(expr, {"receiver", "field_cpp", "field_identity", "is_arrow"})
        match expr.receiver:
            # A construct names its storage or refuses; a call only when it hands one over.
            case receiver if isinstance(receiver, th.THIRCtorCall) or th.record_rvalue_storage(receiver) is not None:
                _require(expr, not write and not expr.is_arrow, "temporary field requires scalar read")
                reference = self.temporary(expr.receiver)
            case th.THIRFieldAccess():
                reference = self.field(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs record storage")
            case th.THIRNarrowedRead():
                reference = self.inline_union(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs union record")
            case th.THIRName() if expr.receiver.name in self.optionals:
                reference = self.optional_name(expr.receiver, extract=True).payload
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs optional record")
            case th.THIRSubscript() if expr.receiver.tuple_index is None:
                # A field of a record element, read in place.
                member = self.element_access(expr.receiver)
                _require(expr, member.kind is MIRValueKind.BORROWED and expr.receiver.form is th.Form.BORROW
                         and unwrap_readonly(unwrap_ref_type(expr.receiver.result_type)) == member.type,
                         "field needs record element")
                reference = th.THIRBorrowedRecord(member.type, member.readonly)
            case th.THIRSubscript():
                reference = self.projection(expr.receiver)
                _require(expr, isinstance(reference, (th.THIRBorrowedRecord, th.THIROwnedRecord)),
                         "field needs tuple reference")
            case _:
                reference = self.references[self.reference_name(expr.receiver, qualified=True)]
        fact = expr.field_identity
        _require(expr, isinstance(fact, th.THIRFieldIdentity), "missing field identity")
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
        if native_container_type(unwrap_readonly(fact.type)):
            # A container field is owned storage of the record; its region is
            # reached through it, and it is never replaced whole.
            _require(expr, not write, "container field replacement is unsupported")
            bare = unwrap_readonly(fact.type)
            _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == bare
                     and expr.form in (th.Form.STORAGE, th.Form.BORROW), "unsupported field type or form")
            layout = self.container_layout(expr, bare)
            element = (th.THIRBorrowedRecord(layout.element.type, readonly)
                       if layout.element.kind is MIRValueKind.BORROWED else layout.element.type)
            return th.THIRNativeContainer(bare, element, readonly)
        if view_leaf(fact.type):
            # A view member stores a loan in the record object, keyed by a
            # member of the record's layout: read whole, or rebound whole
            # (`view_member_write`).
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.BORROW,
                     "unsupported field type or form")
            self.held_layout(expr, reference.type)
            self.held_root(expr)
            return fact.type
        if owned_leaf(fact.type):
            # The field is a place of the record's storage: its buffer is
            # borrowed, copied out or replaced in place, never projected into.
            # Its form is how the C++ spells the member (a BigInt reads as a
            # value scalar, str as storage); either way it is that lvalue.
            _require(expr, expr.result_type == fact.type and expr.form in (th.Form.VALUE, th.Form.STORAGE),
                     "unsupported field type or form")
            self.leaf_layout(expr, fact.type)
            return fact.type
        # A readonly-wrapped owned leaf has no field place of its own type.
        _require(expr, owned_value_type(fact.type) is None, "owned-leaf record field")
        if storage_leaf(fact.type):
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.VALUE,
                     "unsupported field type or form")
            return fact.type
        # An inline record member: a place of the record's storage, borrowed,
        # copied out, or replaced whole in place (`member_write`).
        member = th.THIRBorrowedRecord(unwrap_readonly(fact.type), readonly)
        self.reference(expr, member, expr.result_type)
        return member

    def temporary(self, expr: th.THIRCtorCall | th.THIRCall | th.THIRMethodCall) -> th.THIROwnedRecord:
        """A record rvalue in storage of its full expression: a construct, or
        a call's owned record result."""
        fact = th.record_rvalue_storage(expr)
        _require(expr, self.active_temporaries is not None, "temporary needs full-expression boundary")
        _require(expr, isinstance(fact, th.THIROwnedRecord) and fact.type == expr.result_type
                 and fact.readonly is False, "missing or invalid full-expression storage")
        self.record_value(expr, fact.type, temporary=True, call=not isinstance(expr, th.THIRCtorCall))
        self.active_temporaries.append(expr)
        return fact

    def full_expression(self, expr: th.THIRExpr | th.THIRPrint, *, discard: bool = False,
                        expected: TpyType | None = None,
                        check: Callable[[], TpyType] | None = None) -> TpyType:
        """Check `expr` as one full expression (`check`, else a scalar
        value), recording whether it owns temporaries."""
        assert self.active_temporaries is None
        self.active_temporaries = []
        try:
            with self.argument_order(expr):
                if check is not None:
                    typ = check()
                elif discard and (isinstance(expr, th.THIRCtorCall) or th.record_rvalue_storage(expr) is not None):
                    typ = self.temporary(expr).type
                elif discard and _user_call(expr) and isinstance(expr.result_type, VoidType):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    self.call(expr)
                    typ = expr.result_type
                elif (discard and isinstance(expr, th.THIRMethodCall) and expr.stub_callee is not None
                      and isinstance(expr.result_type, VoidType)):
                    _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
                    self.method_call(expr)
                    typ = expr.result_type
                else:
                    typ = self.expr(expr, expected)
            if self.active_temporaries:
                self.full_expressions.add(expr)
            return typ
        finally:
            self.active_temporaries = None

    def expr(self, expr: th.THIRExpr, expected: TpyType | None = None) -> TpyType:
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
                _require(expr, converted_literal(typ, expr.value) is not None, "unsupported literal value")
            case th.THIRCharLiteral():
                _plain(expr, {"value"})
                _require(expr, leaf_constant(typ, expr.value), "unsupported literal value")
            case th.THIRCall() | th.THIRMethodCall() if _user_call(expr):
                self.call(expr)
                writing = self.call_writes(expr)
            case th.THIRNarrowedRead():
                _require(expr, self.inline_union(expr) == typ, "union scalar extraction required")
            case th.THIRIsinstance():
                _plain(expr, {"variant_cpp", "member_cpps", "union_test"})
                fact = expr.union_test
                _require(expr, typ == BOOL and isinstance(fact, th.THIRUnionTest)
                         and self.unions.get(fact.source) == fact.layout
                         and bool(fact.alternatives) and len(set(fact.alternatives)) == len(fact.alternatives)
                         and all(type(i) is int and 0 <= i < len(fact.layout.elements)
                                 for i in fact.alternatives), "missing or invalid union test")
            case th.THIRModuleVar():
                _plain(expr, {"cpp", "global_binding"})
                self.global_binding(expr)
            case th.THIRName() if expr.global_binding is not None:
                _plain(expr, {"name", "cpp", "global_binding", "is_last_use", "is_movable", "indirect"})
                self.global_binding(expr)
            case th.THIRName():
                if expr.name in self.optionals:
                    _require(expr, self.optional_name(expr, extract=True).payload == typ,
                             "optional scalar extraction required")
                    self.writes[expr] = False
                    return typ
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.name in self.bindings, "non-local name")
                _require(expr, self.bindings[expr.name] == typ, "name type mismatch")
            case th.THIRFieldAccess():
                self.field(expr)
                # A temporary receiver's evaluation may write (a call's
                # arguments); a place receiver evaluates nothing.
                writing = self.writes.get(expr.receiver, False)
            case th.THIRSubscript() if expr.tuple_index is None and self.container_receiver(expr.receiver):
                member = self.element_access(expr)
                _require(expr, member.kind is MIRValueKind.SCALAR and member.type == typ,
                         "element read type mismatch")
                writing = self.writes[expr.index]
            case th.THIRMethodCall() if expr.stub_callee is not None:
                self.method_call(expr)
                writing = self.call_writes(expr)
            case th.THIRSubscript() if expr.tuple_index is None:
                _plain(expr, {"receiver", "index", "bounds_safe"})
                _require(expr, expr.certified_op, "uncertified element read")
                _require(expr, owned_leaf(self.lent_value(expr.receiver)), "element read needs an owned leaf")
                self.value(expr.index)
                writing = self.writes[expr.index]
            case th.THIRSubscript():
                _require(expr, self.projection(expr) == typ, "scalar tuple projection required")
            case th.THIRIsNone():
                if expr.union_monostate:
                    _plain(expr, {"operand", "negate", "union_monostate"})
                    _require(expr, isinstance(expr.operand, th.THIRName) and typ == BOOL
                             and type(expr.negate) is bool, "unsupported union None test")
                    layout = self.union_name(expr.operand)
                    _require(expr, None in layout.elements, "union has no absence alternative")
                    self.writes[expr] = False
                    return typ
                _plain(expr, {"operand", "negate", "value_repr"})
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and isinstance(expr.operand, th.THIRName), "unsupported optional test")
                layout = self.optional_name(expr.operand, extract=False)
                _require(expr, expr.value_repr is (not isinstance(layout.payload, th.THIRBorrowedRecord)),
                         "optional test representation mismatch")
            case th.THIRCoerce() if expr.certified_conversion:
                _plain(expr, {"expr", "coercion_name", "wrap"})
                self.value(expr.expr)
                writing = self.writes[expr.expr]
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name in _LITERAL_COERCIONS
                         and isinstance(expr.expr, th.THIRLiteral), "unsupported coercion")
                _require(expr, self.expr(expr.expr, typ) == typ, "unsupported literal coercion")
            case th.THIRWalrus():
                _require(expr, expr.name not in self.range_counters, "range target write needs separate induction")
                _plain(expr, {"name", "cpp_name", "value", "global_binding"})
                if expr.global_binding is not None:
                    self.global_binding(expr, write=True)
                else:
                    _require(expr, self.bindings.get(expr.name) == typ, "walrus needs existing scalar local")
                before = len(self.active_temporaries or ())
                _require(expr, self.expr(expr.value, typ) == typ, "walrus type mismatch")
                _require(expr, expr.global_binding is None or len(self.active_temporaries or ()) == before,
                         "temporary global assignment")
                writing = True
            case th.THIRUnaryNot():
                _plain(expr, {"operand"})
                _require(expr, typ == BOOL and self.expr(expr.operand) == BOOL,
                         "not requires bool")
                writing = self.writes[expr.operand]
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap", "divisor_non_zero",
                              "both_literal_int_operands"})
                if expr.op in ("&&", "||"):
                    left, right = self.expr(expr.left), self.expr(expr.right)
                    _require(expr, typ == BOOL and left == BOOL and right == BOOL and expr.resolved is None,
                             "unsupported boolean operation")
                else:
                    _require(expr, expr.certified_op, "uncertified binary operation")
                    left, right = self.promotion(expr, *self.operands(expr))
                    if ((self.writes[expr.left] and not _literal(expr.right))
                            or (self.writes[expr.right] and not _literal(expr.left))):
                        raise MIRUnsupported(expr, "order-sensitive eager operands")
                    _require(expr, expr.op not in th.COMPARISON_OPS
                             or typ == BOOL and certified_primitive_comparison(left, right),
                             "comparison operand type mismatch")
                writing = self.writes[expr.left] or self.writes[expr.right]
            case th.THIRUnaryArith():
                _plain(expr, {"cpp_template", "operand", "resolved"})
                _require(expr, expr.certified_op, "uncertified unary operation")
                self.value(expr.operand)
                writing = self.writes[expr.operand]
            case th.THIRValueSelect():
                _plain(expr, {"lhs", "rhs", "op", "lhs_temp_cpp"})
                _require(expr, expr.op in ("&&", "||") and typ == BOOL, "unsupported value select")
                before = len(self.active_temporaries or ())
                _require(expr, self.expr(expr.lhs) == BOOL, "unsupported value select")
                # A hoisted LHS has its own full expression and may extend a subobject's lifetime.
                _require(expr, expr.lhs_temp_cpp is None or len(self.active_temporaries or ()) == before,
                         "temporary in hoisted select operand")
                _require(expr, self.expr(expr.rhs) == BOOL,
                         "unsupported value select")
                # A pure bool select never needs a representation-changing temp.
                _require(expr, expr.lhs_temp_cpp in (None, "auto&&"), "unsupported select temporary")
                writing = self.writes[expr.lhs] or self.writes[expr.rhs]
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                _require(expr, self.expr(expr.cond) == BOOL, "condition requires bool")
                _require(expr, self.expr(expr.then, typ) == typ and self.expr(expr.orelse, typ) == typ,
                         "conditional arm type mismatch")
                writing = any(self.writes[e] for e in (expr.cond, expr.then, expr.orelse))
            case _:
                raise MIRUnsupported(expr, "unsupported expression")
        self.writes[expr] = writing
        return typ

    def promotion(self, expr: th.THIRBinOp, left: TpyType, right: TpyType) -> tuple[TpyType, TpyType]:
        """The operand types a certified operator reads: a promoted operand
        (`THIRBinOp.promoted_operand`) is converted into a fresh value of
        the promotion's type, an owned one in a temporary of the full
        expression."""
        side = expr.promoted_operand
        if side is None:
            return left, right
        target = expr.resolved.promotion.return_type
        if owned_leaf(target):
            _require(expr, self.active_temporaries is not None, "owned temporary needs full-expression boundary")
            self.active_temporaries.append((expr.left, expr.right)[side])
            self.leaf_layout(expr, target)
        return (target, right) if side == 0 else (left, target)

    def operands(self, expr: th.THIRBinOp) -> tuple[TpyType, TpyType]:
        """Check both operands. A number literal converts into the type at
        its own position of the resolved dunder (`1.0 + i` binds the float
        receiver, not i's int32); under a derived comparison, to the other
        operand's type."""
        left, right = expr.left, expr.right
        if isinstance(left, th.THIRLiteral) and not isinstance(right, th.THIRLiteral):
            right_type = self.value(right)
            return self.value(left, expr.operand_position_type(0) or right_type), right_type
        left_type = self.value(left)
        position = expr.operand_position_type(1) if isinstance(right, th.THIRLiteral) else None
        return left_type, self.value(right, position or left_type)

    def stmt(self, stmt: th.THIRStmt, loops: int) -> None:
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
                self.bindings[stmt.alias] = member.type if reference else member
                if reference:
                    self.references[stmt.alias] = member
                else:
                    self.payload_aliases.add(stmt.alias)
            case th.THIRAssign() if stmt.optional_record_assignment is not None:
                _plain(stmt, {"target", "value", "optional_record_assignment"})
                _require(stmt, isinstance(stmt.target, th.THIRName), "optional record assignment needs local")
                _plain(stmt.target, {"name"})
                fact = stmt.optional_record_assignment
                name = stmt.target.name
                _require(stmt, self.optional_record_storage.get(name) == fact
                         and self.bindings.get(name) == stmt.target.result_type == fact.type,
                         "optional record backing mismatch")
                self.record_value(stmt.value, fact.type)
                _require(stmt, self.records[fact.type].layout.movable, "optional backing needs movable record")
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.union_layout is not None:
                _require(stmt, isinstance(stmt, th.THIRAssign) and isinstance(stmt.target, th.THIRName),
                         "unsupported union reseat")
                _plain(stmt, {"target", "value", "union_layout", "union_literal"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name in self.unions and name not in self.parameters
                         and stmt.union_layout == self.unions[name], "union destination mismatch")
                self.union_source(stmt.value, stmt.union_layout, stmt.union_literal)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.optional_layout is not None:
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
                    self.record_value(stmt.value, member.type)
                    _require(stmt, self.records[member.type].layout.movable, "replacement needs movable record")
                else:
                    _require(stmt, not isinstance(stmt, th.THIRAssign) or stmt.slot_cpp is None,
                             "optional slot without replacement storage")
                    self.optional_source(stmt.value, stmt.optional_layout)
            case th.THIRAssign() if stmt.rebind_storage is not None:
                self.replacement(stmt)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                self.borrow_binding(stmt)
            case th.THIRPtrLocalRebind() if _user_call(stmt.value) or isinstance(stmt.value, th.THIRIfExpr):
                self.borrowed_binding(stmt)
            case th.THIRNoOpStmt():
                _plain(stmt, set())
            case th.THIRSetItem():
                self.element_write(stmt)
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and stmt.target.name in self.owned_containers):
                # Rebuilding owned container storage: a replacement of the whole container.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                typ = self.bindings[stmt.target.name]
                _require(stmt, stmt.target.name not in self.parameters, "owned container parameter reseat")
                self.full_expression(stmt.value, check=lambda: self.container_value(stmt.value, typ))
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and stmt.target.name in self.container_views):
                # Reseating a Span holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                readonly = self.full_expression(stmt.value,
                                                check=lambda: self.span_value(stmt.value, self.bindings[name]))
                _require(stmt, not readonly or self.container_views[name], "alias increases access")
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                     and stmt.target.name in self.views):
                # Reseating a view holder: the new source's referents replace the old.
                _plain(stmt, {"target", "value"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name not in self.parameters, "reassigned view parameter")
                self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, self.bindings[name]))
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRName)
                                     and owned_value_type(stmt.target.result_type) is not None):
                _plain(stmt, {"target", "value"})
                target = stmt.target
                if target.global_binding is not None:
                    self.global_binding(target, write=True)
                _plain(target, {"name", "is_last_use", "is_movable"})
                _require(stmt, self.owned_bindings.get(target.name) is True
                         and self.bindings.get(target.name) == target.result_type,
                         "owned-leaf assignment needs owned storage")
                _require(stmt, self.full_expression(stmt.value, check=lambda: self.owned_value(stmt.value, sink=True))
                         == target.result_type, "assignment type mismatch")
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                     and owned_value_type(stmt.target.result_type) is not None):
                # Replaces the field's buffer in place: a write event on the field place.
                _plain(stmt, {"target", "value"})
                typ = self.field(stmt.target, write=True)
                _require(stmt, self.full_expression(stmt.value, check=lambda: self.owned_value(stmt.value, sink=True))
                         == typ, "assignment type mismatch")
            case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                     and record_type(unwrap_readonly(unwrap_ref_type(stmt.target.result_type)))):
                self.member_write(stmt)
            case th.THIRAssign() if isinstance(stmt.target, th.THIRFieldAccess) and view_leaf(stmt.target.result_type):
                self.view_member_write(stmt)
            case th.THIRAssign():
                _plain(stmt, {"target", "value"})
                if isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None:
                    self.global_binding(stmt.target, write=True)
                if isinstance(stmt.target, th.THIRName) and stmt.target.name in self.tuples:
                    _require(stmt, stmt.target.name not in self.parameters, "tuple parameter reseat")
                    _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                    _require(stmt, stmt.target.result_type == self.bindings[stmt.target.name],
                             "tuple destination type mismatch")
                    self.tuple_compatible(stmt, self.tuple_expr(stmt.value), self.tuples[stmt.target.name])
                    return
                _require(stmt, isinstance(stmt.target, (th.THIRName, th.THIRFieldAccess)),
                         "assignment needs local or field target")
                _require(stmt, not isinstance(stmt.target, th.THIRName) or stmt.target.name not in self.payload_aliases,
                         "write through scalar payload alias")
                target_type = (self.field(stmt.target, write=True) if isinstance(stmt.target, th.THIRFieldAccess)
                               else self.expr(stmt.target))
                value_type = self.full_expression(stmt.value, expected=target_type)
                _require(stmt, not (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None
                                   and stmt.value in self.full_expressions), "temporary global assignment")
                _require(stmt, target_type == value_type, "assignment type mismatch")
            case th.THIRExprStmt():
                _plain(stmt, {"expr", "void_cast"})
                self.full_expression(stmt.expr, discard=True)
            case th.THIRReturn():
                _plain(stmt, {"value"})
                if stmt.value is None:
                    _require(stmt, isinstance(self.fn.return_type, VoidType), "missing return value")
                elif self.result is not None and view_leaf(self.result.type):
                    self.full_expression(stmt.value, check=lambda: self.view_value(stmt.value, self.result.type))
                elif self.result is not None and _region_view(self.result.type):
                    readonly = self.full_expression(stmt.value,
                                                    check=lambda: self.span_value(stmt.value, self.result.type))
                    _require(stmt, not readonly or self.result.readonly, "return increases access")
                elif self.container_result is not None:
                    # A container place returned by reference: a borrow of it.
                    _require(stmt, self.container_receiver(stmt.value), "container return needs a place")
                    place = self.container_place(stmt.value)
                    self.element_places[stmt.value] = place
                    _require(stmt, place.type == self.container_result.type
                             and (not place.readonly or self.container_result.readonly), "return increases access")
                elif self.result is not None:
                    self.borrowed_expression(stmt.value, self.result)
                elif self.owned_container_result is not None:
                    self.full_expression(stmt.value, check=lambda: self.owned_container_source(
                        stmt.value, self.owned_container_result))
                elif self.owned_record_result is not None:
                    self.full_expression(stmt.value, check=lambda: self.owned_record_source(
                        stmt.value, self.owned_record_result))
                elif (owned := owned_value_type(self.fn.return_type)) is not None:
                    _require(stmt, self.full_expression(stmt.value, check=lambda: self.owned_value(stmt.value, sink=True))
                             == owned, "return type mismatch")
                else:
                    _require(stmt, self.full_expression(stmt.value, expected=self.fn.return_type) == self.fn.return_type,
                             "return type mismatch")
            case th.THIRForRange():
                self.range_loop(stmt, loops)
            case th.THIRForEach():
                self.native_loop(stmt, loops)
            case th.THIRIf() | th.THIRWhile():
                allowed = {"condition", "then_body", "else_body", "else_is_nested"} if isinstance(
                    stmt, th.THIRIf) else {"condition", "body", "orelse"}
                allowed |= {"hoist_decls", "hoisted_bindings"}
                _plain(stmt, allowed)
                self.hoists(stmt)
                _require(stmt, self.full_expression(stmt.condition) == BOOL, "condition requires bool")
                if isinstance(stmt, th.THIRIf):
                    for arm in (stmt.then_body, stmt.else_body):
                        self.scoped(arm, loops)
                else:
                    self.scoped(stmt.body, loops + 1)
                    self.scoped(stmt.orelse, loops)
            case th.THIRBreak() | th.THIRContinue():
                _plain(stmt, set())
                _require(stmt, loops > 0, "loop control outside loop")
            case th.THIRParamCopy():
                # The body's owned copy of a borrowed parameter it reassigns; reads see the copy.
                _plain(stmt, {"name", "cpp_type", "init_cpp"})
                _require(stmt, any(s is stmt for s in self.fn.body) and stmt.name in self.parameters
                         and self.owned_bindings.get(stmt.name) is False, "param copy needs a borrowed owned-leaf parameter")
                self.owned_bindings[stmt.name] = True
            case th.THIRStrAppend() if stmt.target_expr is not None:
                # A field append: THIR carries the field lvalue, which the emit prefers to `target`.
                _plain(stmt, {"target", "value", "target_expr"})
                _require(stmt, isinstance(stmt.target_expr, th.THIRFieldAccess), "append needs a field place")
                _require(stmt, primitive_owned_leaf(self.field(stmt.target_expr, write=True)),
                         "append needs owned storage")
                self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
                # Python reads the field before the value runs; C++ appends to
                # whatever the value left there.
                _require(stmt, not self.writes[stmt.value], "order-sensitive eager operands")
            case th.THIRStrAppend():
                _plain(stmt, {"target", "value"})
                _require(stmt, self.owned_bindings.get(stmt.target) is True and primitive_owned_leaf(self.bindings[stmt.target]),
                         "append needs owned storage")
                self.full_expression(stmt.value, check=lambda: self.value(stmt.value))
            case th.THIRPrint():
                _plain(stmt, {"args", "sep_value", "end_value"})
                _require(stmt, all(v is None or isinstance(v, str) for v in (stmt.sep_value, stmt.end_value)),
                         "print separator needs a literal")
                # The print call is one full expression: its arguments' temporaries
                # (a call's owned-leaf result, a built operand) live until it returns.
                self.full_expression(stmt, check=lambda: self.print_arguments(stmt))
            case _:
                raise MIRUnsupported(stmt, "unsupported statement")

    def owned_record_source(self, expr: th.THIRExpr, typ: NominalType) -> TpyType:
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
            return typ
        self.record_value(expr, typ, call=True)
        return typ

    def owned_container_source(self, expr: th.THIRExpr, typ: NominalType) -> TpyType:
        """What an `Own[...]` container result takes: the body's own
        container storage moved out, or a literal built for it."""
        if isinstance(expr, th.THIRName) and expr.global_binding is None and expr.name in self.owned_containers:
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, self.bindings[expr.name] == typ and expr.form in (th.Form.BORROW, th.Form.STORAGE),
                     "return type mismatch")
            return typ
        return self.container_value(expr, typ)

    def element_write(self, stmt: th.THIRSetItem) -> None:
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
            self.full_expression(stmt.value, check=lambda: self.setitem_call(stmt, callee))
            return

        def check() -> TpyType:
            member = self.element_access(target)
            _require(stmt, not self.element_places[target].readonly, "readonly element store")
            match member.kind:
                case MIRValueKind.SCALAR:
                    _require(stmt, self.expr(stmt.value, member.type) == member.type, "assignment type mismatch")
                case MIRValueKind.OWNED:
                    _require(stmt, self.owned_value(stmt.value, sink=True) == member.type, "assignment type mismatch")
                case _:
                    self.record_value(stmt.value, member.type, call=True)
            self.argument_order_rule(stmt, (target.index, stmt.value))
            return member.type
        self.full_expression(stmt.value, check=check)

    def setitem_call(self, stmt: th.THIRSetItem, callee: th.THIRStubCallee) -> TpyType:
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
        self.element_places[target.receiver] = place
        receiver = summary.parameters[0]
        _require(stmt, receiver.type == place.type and not place.readonly, "readonly element store")
        for arg, binding in zip((target.index, stmt.value), summary.parameters[1:]):
            _require(arg, not binding.protocol, "unsupported element call parameter")
            self.argument(arg, binding)
        self.argument_order_rule(stmt, (target.index, stmt.value))
        self.calls[stmt] = summary
        return callee.signature.return_type

    def print_arguments(self, stmt: th.THIRPrint) -> TpyType:
        for arg in stmt.args:
            _plain(arg, {"expr", "print_form"})
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS | _OWNED_PRINT_FORMS,
                     "print argument needs a scalar leaf")
            typ = self.value(arg.expr)
            # A view prints the leaf it views.
            typ = through_view(typ)
            _require(arg.expr, arg.print_form in _SCALAR_PRINT_FORMS and primitive_leaf(typ)
                     or arg.print_form in _OWNED_PRINT_FORMS and primitive_owned_leaf(typ),
                     "print argument needs a scalar leaf")
        # The arguments of the variadic print call evaluate in an unspecified order.
        self.argument_order_rule(stmt, tuple(arg.expr for arg in stmt.args))
        return VoidType()

    def range_loop(self, stmt: th.THIRForRange, loops: int) -> None:
        _plain(stmt, {"var", "elem_type", "start", "stop", "start_is_literal", "stop_is_literal",
                      "body", "step_kind", "orelse", "hoist_loop_var", "target_written",
                      "hoist_decls", "hoisted_bindings"})
        _require(stmt, stmt.elem_type == INT32 and stmt.step_kind in ("plus_one", "unit_neg"),
                 "range needs int32 unit step")
        _require(stmt, isinstance(stmt.var, str) and bool(stmt.var) and isinstance(stmt.stop, th.THIRExpr),
                 "missing range target or stop")
        _require(stmt, type(stmt.hoist_loop_var) is bool and type(stmt.target_written) is bool,
                 "invalid range binding facts")
        self.hoists(stmt)
        for bound, literal in ((stmt.start, stmt.start_is_literal), (stmt.stop, stmt.stop_is_literal)):
            _require(stmt, type(literal) is bool and literal is (bound is None or _literal(bound)),
                     "range bound capture disagrees with expression")
            if bound is not None:
                _require(bound, (_literal(bound) or isinstance(bound, (th.THIRName, th.THIRFieldAccess, th.THIRSubscript)))
                         and self.expr(bound, INT32) == INT32, "unsupported range bound")
        _require(stmt, stmt.var not in self.range_counters, "nested range target changes outer induction")
        _require(stmt, self.bindings.get(stmt.var) == INT32 if stmt.hoist_loop_var else stmt.var not in self.bindings,
                 "range target residence mismatch")
        bindings, counters = self.bindings.copy(), self.range_counters.copy()
        self.bindings[stmt.var] = INT32
        if not stmt.hoist_loop_var and not stmt.target_written:
            self.range_counters.add(stmt.var)
        self.scoped(stmt.body, loops + 1)
        self.bindings, self.range_counters = bindings, counters
        self.scoped(stmt.orelse, loops)

    def native_loop(self, stmt: th.THIRForEach, loops: int) -> None:
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
        self.element_places[stmt.iterable] = place
        source = fact.source
        _require(stmt, isinstance(source, th.THIRNativeContainer), "missing or invalid native iteration facts")
        # A cursor binds the source's declared element, one at a time.
        _require(stmt, binds_cursor(place.type), "missing or invalid native iteration facts")
        element = place.layout.element
        _require(stmt, isinstance(source, th.THIRNativeContainer) and source.type == place.type
                 and source.readonly == place.readonly
                 and source.element == (th.THIRBorrowedRecord(element.type, element.readonly)
                                        if element.kind is MIRValueKind.BORROWED else element.type),
                 "native iteration source disagrees with binding")
        reference = (th.THIRBorrowedRecord(element.type, element.readonly)
                     if element.kind is MIRValueKind.BORROWED else None)
        # An owned-leaf element is read through a view of it, or a readonly borrow.
        view = element.kind is MIRValueKind.OWNED and view_leaf(stmt.elem_type)
        typ = stmt.elem_type if view else element.type
        _require(stmt, (view_compatible(stmt.elem_type, element.type) if view
                        else unwrap_readonly(stmt.elem_type) == typ)
                 and fact.binding is loop_binding_kind(stmt.elem_type, stmt.const_loop_var, hoisted=stmt.hoist_loop_var)
                 and fact.binding in (LoopBinding.VALUE, LoopBinding.ASSIGN, LoopBinding.REFERENCE, LoopBinding.CONST_REFERENCE)
                 and (reference is None or not stmt.hoist_loop_var and stmt.const_loop_var == reference.readonly)
                 and (element.kind is not MIRValueKind.OWNED or not stmt.hoist_loop_var and (
                     view or fact.binding is LoopBinding.CONST_REFERENCE)),
                 "unsupported native target binding")
        self.hoists(stmt)
        _require(stmt, stmt.var not in self.range_counters and stmt.var not in self.iteration_references,
                 "nested loop replaces outer iteration binding")
        _require(stmt, self.bindings.get(stmt.var) == typ if stmt.hoist_loop_var else stmt.var not in self.bindings,
                 "native target residence mismatch")
        bindings, references, targets = self.bindings.copy(), self.references.copy(), self.iteration_references.copy()
        views, owned = self.views.copy(), self.owned_bindings.copy()
        self.bindings[stmt.var] = typ
        if reference:
            self.references[stmt.var] = reference
            self.iteration_references.add(stmt.var)
        elif view:
            self.views.add(stmt.var)
            self.iteration_references.add(stmt.var)
        elif element.kind is MIRValueKind.OWNED:
            self.owned_bindings[stmt.var] = False
            self.iteration_references.add(stmt.var)
        self.scoped(stmt.body, loops + 1)
        self.bindings, self.references, self.iteration_references = bindings, references, targets
        self.views, self.owned_bindings = views, owned
        self.scoped(stmt.orelse, loops)

    def hoists(self, stmt: th.THIRIf | th.THIRWhile | th.THIRForRange | th.THIRForEach) -> None:
        facts = stmt.hoisted_bindings
        _require(stmt, all(isinstance(f, th.THIRHoistedBinding) for f in facts)
                 and tuple(f.name for f in facts) == tuple(decl.name for decl in stmt.hoist_decls),
                 "missing or inconsistent hoisted binding facts")
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
                self.references[fact.name] = reference
                self.optional_record_storage[fact.name] = reference
                typ = reference.type
            elif fact.borrowed_record is not None:
                self.reference(stmt, fact.borrowed_record, fact.type)
                self.references[fact.name] = fact.borrowed_record
                typ = fact.borrowed_record.type
            elif fact.optional_layout is not None:
                self.optional_layout(stmt, fact.optional_layout, fact.type)
                self.optionals[fact.name] = fact.optional_layout
                typ = fact.type
            elif fact.union_layout is not None:
                self.union_layout(stmt, fact.union_layout, fact.type)
                _require(stmt, all(m is None or storage_leaf(m) for m in fact.union_layout.elements),
                         "unsupported hoisted union storage")
                self.unions[fact.name] = fact.union_layout
                typ = fact.type
            elif fact.tuple_layout is not None:
                self.tuple_layout(stmt, fact.tuple_layout, fact.type)
                self.tuples[fact.name] = fact.tuple_layout
                typ = fact.type
            else:
                _require(stmt, storage_leaf(fact.type), "unsupported hoisted value")
                typ = fact.type
            wrapper = (fact.union_layout is not None or fact.optional_layout is not None
                       and storage_leaf(fact.optional_layout.payload))
            if wrapper:
                default = fact.physical_default
                first = fact.union_layout.elements[0] if fact.union_layout is not None else None
                expected = None if first is None else zero_value_of(first)
                _require(stmt, (first is None or expected is not None)
                         and isinstance(default, th.THIRWrapperDefault)
                         and type(default.alternative) is int and default.alternative == 0
                         and type(default.value) is type(expected) and default.value == expected,
                         "missing or inconsistent physical wrapper default")
            else:
                _require(stmt, fact.physical_default is None, "physical default on non-wrapper hoist")
            self.bindings[fact.name] = typ

    def scoped(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> None:
        containers = self.containers.copy()
        tuple_roots = self.tuple_roots.copy()
        saved = (self.bindings.copy(), self.references.copy(), self.payload_aliases.copy(),
                 self.tuples.copy(), self.optionals.copy(), self.unions.copy(), self.fixed_owned.copy(),
                 self.optional_record_storage.copy())
        owned = self.owned_bindings.copy()
        views = self.views.copy()
        owned_containers, container_views = self.owned_containers.copy(), self.container_views.copy()
        self.declarations(stmts, loops)
        self.views = views
        self.owned_containers, self.container_views = owned_containers, container_views
        retained_fixed = saved[-2] & self.fixed_owned
        (self.bindings, self.references, self.payload_aliases,
         self.tuples, self.optionals, self.unions, self.fixed_owned, self.optional_record_storage) = saved
        self.owned_bindings = owned
        self.fixed_owned = retained_fixed
        self.tuple_roots = tuple_roots
        self.containers = containers


@dataclass
class _Block:
    id: MIRBlockId
    region: MIRRegionId
    statements: list[MIRStatement] = field(default_factory=list)
    terminator: MIRTerminator | None = None


class _Builder:
    def __init__(self, body: MIRBodyId, fn: th.THIRFunction, coverage: _Coverage) -> None:
        self.body = body
        self.fn = fn
        self.slots: list[MIRSlot] = []
        self.constant_bools: dict[MIRSlotId, bool] = {}
        self.bindings: dict[str, MIRSlotId] = {}
        self.blocks: list[_Block] = []
        self.region = MIRRegionId(body, 0)
        self.regions = [MIRRegion(self.region, None, MIRBlockId(body, 0))]
        self.current: _Block | None = self.block()
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []
        self.records = coverage.records
        self.borrowed_bindings = coverage.borrowed_bindings
        self.tuple_exprs = coverage.tuple_exprs
        self.storage: dict[str, MIRSlotId] = {}
        self.global_facts = coverage.globals
        self.globals: dict[MIRGlobalId, MIRSlotId] = {}
        self.full_expressions = coverage.full_expressions
        self.types = coverage.types
        self.calls = coverage.calls
        self.borrowed_result = coverage.result
        self.record_sources = coverage.record_sources
        self.record_temporaries = IdentityMap((*coverage.argument_temporaries.items(),
                                              *coverage.select_temporaries.items()))
        self.temp_plan = fn.temp_plan if self.record_temporaries else None
        self.temp_regions = {0: self.region}
        self.temp_scope = 0
        self.temp_storage: dict[int, MIRSlotId] = {}
        self.temp_holders: dict[int, MIRSlotId] = {}
        self.backing_places: IdentityMap[th.THIRExpr, MIRPlace] = IdentityMap()
        self.borrow_operations: IdentityMap[th.THIRStmt, tuple[MIRPoint, ...]] = IdentityMap()
        self.layouts = coverage.layouts
        self.element_places = coverage.element_places
        self.element_bindings = coverage.element_bindings
        self.span_holders = coverage.span_holders
        self.container_result = coverage.container_result
        self.owned_container_result = coverage.owned_container_result
        self.owned_record_result = coverage.owned_record_result

    def declare_temporaries(self, stmt: th.THIRStmt) -> None:
        if self.temp_plan is None:
            return
        for placement in self.temp_plan.declarations_in(stmt, self.temp_scope):
            reference = self.record_temporaries[placement.node]
            assert self.temp_regions[placement.scope] == self.region
            storage = self.slot(reference.type, MIRSlotKind.LOCAL, storage=True,
                                storage_duration=self.region if self.region.index else MIRStorageDuration.BODY,
                                record_storage=(MIRRecordStorageKind.OPTIONAL if placement.optional
                                                else MIRRecordStorageKind.DIRECT))
            self.temp_storage[placement.index] = storage
            self.backing_places[placement.node] = MIRPlace(storage)
            self.temp_holders[placement.index] = self.slot(reference.type, reference=reference)
            if placement.optional:
                self.current.statements.append(MIRRecordStorageInit(MIRPlace(storage), placement.node.loc))
        self.initialize_temporaries(stmt)

    def initialize_temporaries(self, anchor: th.THIRNode) -> None:
        if self.temp_plan is None:
            return
        for placement in self.temp_plan.initializations.get(anchor, ()):
            storage = self.temp_storage[placement.index]
            mode = MIRRecordWriteMode.OPTIONAL_ASSIGN if placement.optional else self.initial_mode()
            value = placement.node.init if isinstance(placement.node, th.THIRArgTemp) else placement.node.value
            self.write(storage, self.record_value(value), placement.node.loc, MIRRecordWrite(mode))
            self.write(self.temp_holders[placement.index], MIRBorrow(MIRPlace(storage)), placement.node.loc)

    def block(self) -> _Block:
        block = _Block(MIRBlockId(self.body, len(self.blocks)), self.region)
        self.blocks.append(block)
        return block

    def slot(self, typ: TpyType, kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
             name: str | None = None, reference: th.THIRBorrowedRecord | None = None,
             *, storage: bool = False, tuple_layout: th.THIRTupleLayout | None = None,
             optional_layout: th.THIROptionalLayout | None = None,
             union_layout: th.THIRUnionLayout | None = None,
             alias_source: MIRPlace | None = None,
             global_binding: th.THIRGlobalBinding | None = None,
             container: th.THIRNativeContainer | None = None, iterator: bool = False,
             storage_duration: MIRStorageDuration | MIRRegionId | None = None,
             record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT,
             passing: ParamPassing | None = None,
             layout: MIRContainerLayout | None = None) -> MIRSlotId:
        sid = MIRSlotId(self.body, len(self.slots))
        self.slots.append(MIRSlot(sid, container.type if container else reference.type if reference else (
            unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
            if optional_layout is not None or union_layout is not None or tuple_layout is not None else typ), kind, name,
                                  form=(th.Form.STORAGE if storage or tuple_layout is not None and tuple_layout.owns_records
                                        else th.Form.BORROW if reference or alias_source or container else th.Form.VALUE),
                                  value_kind=(MIRValueKind.NATIVE_ITERATOR if iterator else
                                              MIRValueKind.BORROWED_CONTAINER if container else
                                              MIRValueKind.OWNED if storage else
                                              MIRValueKind.BORROWED if reference else
                                              MIRValueKind.TUPLE if tuple_layout is not None else
                                              MIRValueKind.UNION if union_layout is not None else
                                              MIRValueKind.PAYLOAD_ALIAS if alias_source is not None else
                                              MIRValueKind.OPTIONAL if optional_layout is not None else MIRValueKind.SCALAR),
                                  readonly=(reference.readonly if reference else
                                            not global_binding.writable if global_binding is not None else
                                            container.readonly if container else alias_source is not None),
                                  container_layout=(with_access(self.layouts[container.type], container.readonly)
                                                    if container else layout),
                                  tuple_layout=self.layout(tuple_layout) if tuple_layout is not None else None,
                                  optional_layout=self.optional_layout(optional_layout)
                                  if optional_layout is not None else None,
                                  union_layout=MIRUnionLayout(tuple(
                                      None if member is None else self.payload(member)
                                      for member in union_layout.elements)) if union_layout is not None else None,
                                  alias_source=alias_source,
                                  global_id=MIRGlobalId(global_binding.module, global_binding.name)
                                  if global_binding is not None else None,
                                  storage_duration=storage_duration,
                                  record_storage=record_storage, passing=passing,
                                  residence=(self.regions[0].id if storage_duration is MIRStorageDuration.BODY else
                                             storage_duration if storage and isinstance(storage_duration, MIRRegionId)
                                             else self.region) if kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
                                  else None))
        return sid

    @staticmethod
    def optional_layout(layout: th.THIROptionalLayout) -> MIROptionalLayout:
        member = layout.payload
        return (MIROptionalLayout(member.type, MIRValueKind.BORROWED, member.readonly)
                if isinstance(member, th.THIRBorrowedRecord) else MIROptionalLayout(member))

    def optional_value(self, expr: th.THIRExpr | None, layout: th.THIROptionalLayout) -> MIRRvalue:
        if expr is None or isinstance(expr, th.THIRLiteral) and expr.value is None:
            return MIROptionalConstruct()
        if _literal(expr):
            literal = expr.expr if isinstance(expr, th.THIRCoerce) else expr
            return MIROptionalConstruct(self.result(layout.payload, MIRConstant(literal.value), expr.loc))
        if isinstance(expr, th.THIRName) and expr.optional_read is not None and not expr.optional_read.extract:
            return MIROptionalCopy(self.bindings[expr.name])
        if isinstance(layout.payload, th.THIRBorrowedRecord):
            return MIROptionalConstruct(self.bindings[expr.name])
        return MIROptionalConstruct(self.expr(expr))

    @staticmethod
    def payload(member: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> MIRTupleElement:
        match member:
            case th.THIRBorrowedRecord():
                return MIRTupleElement(member.type, MIRValueKind.BORROWED, member.readonly)
            case th.THIROwnedRecord():
                return MIRTupleElement(member.type, MIRValueKind.OWNED, member.readonly)
            case _:
                return MIRTupleElement(member)

    def layout(self, layout: th.THIRTupleLayout) -> MIRTupleLayout:
        return MIRTupleLayout(tuple(self.payload(member) for member in layout.elements))

    def union_value(self, expr: th.THIRExpr, layout: th.THIRUnionLayout,
                    literal: th.THIRUnionLiteral | None = None) -> MIRRvalue:
        if literal is not None:
            member = layout.elements[literal.alternative]
            source = None if member is None else self.result(member, MIRConstant(literal.value), expr.loc)
            return MIRUnionConstruct(literal.alternative, source)
        if isinstance(expr, th.THIRName) and expr.union_read is not None:
            return MIRUnionCopy(self.bindings[expr.name])
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            return MIRUnionConstruct(layout.elements.index(None))
        for index, member in enumerate(layout.elements):
            if isinstance(member, th.THIRBorrowedRecord) and isinstance(expr, th.THIRName):
                source = self.bindings[expr.name]
                if self.slots[source.index].type == member.type:
                    return MIRUnionConstruct(index, source)
            elif member == self.types[expr]:
                return MIRUnionConstruct(index, self.expr(expr))
        raise AssertionError("coverage and union construction disagree")

    def union_place(self, fact: th.THIRUnionExtraction) -> MIRPlace:
        return MIRPlace(self.bindings[fact.source], (MIRUnionPayload(fact.alternative),))

    def tuple_expr(self, expr: th.THIRExpr) -> MIRSlotId:
        dest = self.slot(expr.result_type, tuple_layout=self.tuple_exprs[expr])
        if isinstance(expr, th.THIRName):
            value = MIRTupleCopy(self.bindings[expr.name])
        else:
            value = MIRTupleConstruct(tuple(
                self.place(e).root if isinstance(member, th.THIRBorrowedRecord) else self.expr(e)
                for e, member in zip(expr.elements, self.tuple_exprs[expr].elements)))
        self.write(dest, value, expr.loc)
        return dest

    def write(self, dest: MIRSlotId | MIRPlace, value: MIRRvalue, loc: SourceLocation | None,
              storage_write: MIRRecordWrite | MIRPayloadWrite | MIRTupleInitialization | None = None) -> None:
        assert self.current is not None
        target = MIRPlace(dest) if isinstance(dest, MIRSlotId) else dest
        self.current.statements.append(MIRAssign(target, value, loc, storage_write))

    def borrow_operation(self, stmt: th.THIRStmt) -> None:
        assert self.current is not None
        point = MIRPoint(self.current.id, len(self.current.statements))
        self.borrow_operations[stmt] = (*self.borrow_operations.get(stmt, ()), point)

    def payload_write(self, dest: MIRSlotId, mode: MIRPayloadWriteMode) -> MIRPayloadWrite | None:
        if mode is MIRPayloadWriteMode.INITIALIZE and self.region.index != 0:
            mode = MIRPayloadWriteMode.INITIALIZE_REGION
        return MIRPayloadWrite(mode) if scalar_wrapper(self.slots[dest.index]) else None

    def place(self, expr: th.THIRExpr) -> MIRPlace:
        if isinstance(expr, (th.THIRName, th.THIRModuleVar)) and expr.global_binding is not None:
            fact = expr.global_binding
            return MIRPlace(self.globals[MIRGlobalId(fact.module, fact.name)])
        match expr:
            case _ if th.record_rvalue_storage(expr) is not None:
                storage = self.slot(expr.result_type, storage=True, storage_duration=self.region)
                self.backing_places[expr] = MIRPlace(storage)
                self.write(storage, self.record_value(expr), expr.loc,
                           MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                return MIRPlace(storage)
            case th.THIRSelf():
                return MIRPlace(self.bindings["self"])
            case th.THIRNarrowedRead():
                return self.union_place(expr.union_extraction)
            case th.THIRName():
                if expr.optional_read is not None and expr.optional_read.extract:
                    return MIRPlace(self.bindings[expr.name], (MIROptionalPayload(),))
                return MIRPlace(self.bindings[expr.name])
            case th.THIRSubscript() if expr in self.element_places:
                return self.element_place(expr)
            case th.THIRSubscript():
                root = (self.bindings[expr.receiver.name] if isinstance(expr.receiver, th.THIRName)
                        else self.tuple_expr(expr.receiver))
                return MIRPlace(root, (MIRTupleIndex(expr.tuple_index),))
            case _:
                assert isinstance(expr, th.THIRFieldAccess) and expr.field_identity is not None
                member = expr.field_identity
                base = self.place(expr.receiver)
                # An element place is storage itself, like an inline member.
                inline = isinstance(expr.receiver, th.THIRFieldAccess) or (
                    th.record_rvalue_storage(expr.receiver) is not None) or (
                    expr.receiver in self.element_places) or (
                    isinstance(expr.receiver, th.THIRSubscript) and isinstance(
                        self.tuple_exprs[expr.receiver.receiver].elements[expr.receiver.tuple_index], th.THIROwnedRecord))
                deref = () if inline else (MIRDeref(),)
                return MIRPlace(base.root, base.projections + deref + (
                    MIRField(MIRFieldId(member.owner, member.name), member.type),))

    def container_root(self, expr: th.THIRExpr) -> MIRPlace:
        """The place of a container: a binding's slot (owned storage, a
        borrowed container or a Span holder, each reaching its region
        directly), or a container field of a record."""
        if isinstance(expr, th.THIRName):
            return MIRPlace(self.bindings[expr.name])
        if isinstance(expr, (th.THIRCall, th.THIRMethodCall)):
            return MIRPlace(self.container_slot(expr))
        return self.place(expr)

    def container_slot(self, expr: th.THIRExpr) -> MIRSlotId:
        """A slot holding a container place, for an operation that takes a
        holder (a call, an iterator): a binding itself, a borrowed container
        holder of a field, or the view holder a stub's view result fills."""
        if isinstance(expr, th.THIRName):
            return self.bindings[expr.name]
        place = self.element_places[expr]
        element = place.layout.element
        container = th.THIRNativeContainer(place.type, th.THIRBorrowedRecord(element.type, element.readonly)
                                           if element.kind is MIRValueKind.BORROWED else element.type, place.readonly)
        if _user_call(expr):
            # The container a user call returns by reference: a holder of the call's result.
            self.initialize_temporaries(expr)
            holder = self.slot(place.type, container=container)
            self.write(holder, self.call(expr), expr.loc)
            return holder
        if isinstance(expr, th.THIRMethodCall):
            self.initialize_temporaries(expr)
            holder = self.container_view_slot(place.type, place.readonly)
            self.write(holder, self.call(expr), expr.loc)
            return holder
        holder = self.slot(place.type, container=container)
        self.write(holder, MIRBorrow(self.place(expr)), expr.loc)
        return holder

    def element_place(self, expr: th.THIRSubscript) -> MIRPlace:
        """Evaluate a subscript's index or key, then name the container's
        element region: any element, never one by identity."""
        place = self.element_places[expr]
        if place.layout.value is None or storage_leaf(place.layout.element.type):
            self.expr(expr.index)
        else:
            # An owned-leaf key is read by the lookup (its hash and its
            # comparisons), so its holder stays live through the access.
            self.result(BOOL, MIROp("key", (self.operand(expr.index),), may_raise=True), expr.index.loc)
        root = self.container_root(expr.receiver)
        return MIRPlace(root.root, (*root.projections, MIRContainerElements()))

    def chain_raises(self, expr: th.THIRExpr) -> bool:
        """Whether reading a field place may raise: it reaches through an
        element subscript whose index may be out of range."""
        while isinstance(expr, th.THIRFieldAccess):
            expr = expr.receiver
        return isinstance(expr, th.THIRSubscript) and expr in self.element_places and self.element_raises(expr)

    def element_raises(self, expr: th.THIRSubscript) -> bool:
        """Whether an element access may raise: an index not proven in
        range, or any key (a missing key raises)."""
        return not expr.bounds_safe or self.element_places[expr].layout.value is not None

    def span_rvalue(self, expr: th.THIRExpr, holder: MIRSlotId) -> MIRRvalue:
        """The value a Span holder takes: an alias of a Span of its own
        type, else a borrow of the region its source views (an unstepped
        slice reads its bounds first)."""
        match expr:
            case th.THIRName() if self.slots[self.bindings[expr.name].index].type == self.slots[holder.index].type:
                return MIRAlias(self.bindings[expr.name])
            case th.THIRName():
                return MIRBorrow(MIRPlace(self.bindings[expr.name], (MIRContainerElements(),)))
            case th.THIRCoerce():
                return self.span_rvalue(expr.expr, holder)
            case _:
                assert isinstance(expr, th.THIRStrSlice)
                for bound in (expr.lower, expr.upper):
                    if bound is not None:
                        self.expr(bound)
                root = self.container_root(expr.receiver)
                return MIRBorrow(MIRPlace(root.root, (*root.projections, MIRContainerElements())))

    def container_view_slot(self, typ: NominalType, readonly: bool, kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
                            name: str | None = None, passing: ParamPassing | None = None) -> MIRSlotId:
        fact = _container_view_fact(typ, readonly)
        return self.slot(typ, kind, name, fact, layout=with_access(self.layouts[typ], fact.readonly), passing=passing)

    def end(self, term: MIRTerminator) -> None:
        assert self.current is not None and self.current.terminator is None
        self.current.terminator = term
        self.current = None

    def borrowed_expression(self, expr: th.THIRExpr, result: th.THIRBorrowedRecord) -> MIRSlotId:
        self.initialize_temporaries(expr)
        match expr:
            case th.THIRSlotEmplace():
                return self.temp_holders[self.temp_plan.placement(expr).index]
            case th.THIRCall() | th.THIRMethodCall():
                dest = self.slot(result.type, reference=result)
                self.write(dest, self.call(expr), expr.loc)
                return dest
            case th.THIRFieldAccess():
                # A member place: the result holder borrows it.
                dest = self.slot(result.type, reference=result)
                self.write(dest, MIRBorrow(self.place(expr), self.chain_raises(expr)), expr.loc)
                return dest
            case th.THIRIfExpr():
                condition = self.expr(expr.cond)
                dest = self.slot(result.type, reference=result)
                yes, no, join = self.block(), self.block(), self.block()
                self.branch(condition, yes.id, no.id, expr.loc)
                for block, arm in ((yes, expr.then), (no, expr.orelse)):
                    self.current = block
                    source = self.borrowed_expression(arm, result)
                    self.write(dest, MIRAlias(source), arm.loc)
                    self.end(MIRGoto(join.id, arm.loc))
                self.current = join
                return dest
            case _:
                return self.bindings[expr.name if isinstance(expr, th.THIRName) else "self"]

    def result(self, typ: TpyType, value: MIRRvalue, loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        self.write(dest, value, loc)
        # Only these expression temporaries have one definition; locals and select results can change.
        if typ == BOOL:
            match value:
                case MIRConstant(value=constant) if type(constant) is bool:
                    self.constant_bools[dest] = constant
                case MIRNot(operand=operand) if operand in self.constant_bools:
                    self.constant_bools[dest] = not self.constant_bools[operand]
        return dest

    def branch(self, cond: MIRSlotId, yes: MIRBlockId, no: MIRBlockId,
               loc: SourceLocation | None) -> None:
        if cond in self.constant_bools:
            self.end(MIRGoto(yes if self.constant_bools[cond] else no, loc))
        else:
            self.end(MIRBranch(cond, yes, no, loc))

    def select(self, cond: MIRSlotId, then: th.THIRExpr | MIRSlotId,
               otherwise: th.THIRExpr | MIRSlotId, typ: TpyType,
               loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        yes, no, join = self.block(), self.block(), self.block()
        self.branch(cond, yes.id, no.id, loc)
        for block, arm in ((yes, then), (no, otherwise)):
            self.current = block
            value = arm if isinstance(arm, MIRSlotId) else self.expr(arm)
            self.write(dest, MIRRead(MIRPlace(value)), loc)
            self.end(MIRGoto(join.id, loc))
        self.current = join
        return dest

    def call(self, expr: th.THIRCall | th.THIRMethodCall) -> MIRCall:
        summary = self.calls[expr]
        # A method's receiver is parameter 0, lent as the holder of its place.
        arguments = tuple(self.argument(arg, binding)
                          for arg, binding in zip(_call_arguments(expr), summary.parameters))
        return MIRCall(summary, arguments, not summary.normal_return_only)

    def argument(self, arg: th.THIRExpr, binding: MIRParameterBinding) -> MIRSlotId:
        if binding.borrowed_record is not None:
            if isinstance(arg, th.THIRArgTemp):
                return self.temp_holders[self.temp_plan.placement(arg).index]
            return self.record_holder(arg)
        if arg in self.element_places and not isinstance(arg, th.THIRSubscript):
            # A container place: lent whole, through a holder of it.
            return self.container_slot(arg)
        if isinstance(arg, th.THIRMove) and native_container_type(binding.type):
            # Owned container storage moved into the temporary the callee takes over.
            typ = binding.type
            temporary = self.slot(typ, storage=True, storage_duration=self.region,
                                  layout=with_access(self.layouts[typ], False))
            self.write(temporary, MIRMove(self.bindings[arg.value.name]), arg.loc,
                       MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
            return temporary
        if binding.protocol:
            # A protocol parameter reads its argument: a leaf lent in place, or a scalar.
            return self.operand(arg)
        if view_leaf(binding.type):
            return self.view_slot(arg)
        if record_type(binding.type) and binding.borrowed_record is None and binding.passing in OWNING_PASSINGS:
            # A record handed over by value: the temporary the call moves from.
            return self.record_temporary(arg)
        if owned_value_type(binding.type) is None:
            return self.expr(arg)
        return self.owned_argument(arg, binding.type, binding.passing)

    def owned_argument(self, arg: th.THIRExpr, typ: TpyType, passing: ParamPassing) -> MIRSlotId:
        if passing in BORROWING_PASSINGS:
            # Lent for the call: the holder stays live until the call reads it.
            return self.operand(arg)
        # The callee's own copy: storage of the full expression, handed to the call.
        assert self.region.index != 0
        temporary = self.slot(typ, storage=True, storage_duration=self.region)
        self.write(temporary, self.owned_rvalue(arg), arg.loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return temporary

    def expr(self, expr: th.THIRExpr) -> MIRSlotId:
        self.initialize_temporaries(expr)
        typ = self.types[expr]
        loc = expr.loc
        match expr:
            case th.THIRCall():
                return self.result(typ, self.call(expr), loc)
            case th.THIRLiteral():
                return self.result(typ, MIRConstant(converted_literal(typ, expr.value)), loc)
            case th.THIRCharLiteral():
                return self.result(typ, MIRConstant(expr.value), loc)
            case th.THIRCoerce() if expr.certified_conversion:
                return self.result(typ, self.conversion(expr), loc)
            case th.THIRCoerce():
                return self.result(typ, MIRConstant(expr.expr.value), loc)
            case th.THIRSubscript() if expr in self.element_places:
                # A scalar element read; it may raise when its index is unproven.
                return self.result(typ, MIRRead(self.element_place(expr), self.element_raises(expr)), loc)
            case th.THIRMethodCall():
                self.initialize_temporaries(expr)
                return self.result(typ, self.call(expr), loc)
            case th.THIRSubscript() if expr.tuple_index is None:
                # An element read of an owned leaf may raise (an index out of range).
                return self.result(typ, MIROp("getitem", (self.operand(expr.receiver), self.expr(expr.index)),
                                              may_raise=True), loc)
            case th.THIRName() | th.THIRModuleVar() | th.THIRFieldAccess() | th.THIRSubscript() | th.THIRNarrowedRead():
                return self.result(typ, MIRRead(self.place(expr), self.chain_raises(expr)), loc)
            case th.THIRIsinstance():
                fact = expr.union_test
                return self.result(BOOL, MIRIsAlternative(self.bindings[fact.source], fact.alternatives), loc)
            case th.THIRWalrus():
                value = self.expr(expr.value)
                fact = expr.global_binding
                target = (self.globals[MIRGlobalId(fact.module, fact.name)] if fact is not None
                          else self.bindings[expr.name])
                self.write(target, MIRRead(MIRPlace(value)), loc)
                return value
            case th.THIRUnaryNot():
                return self.result(BOOL, MIRNot(self.expr(expr.operand)), loc)
            case th.THIRIsNone():
                if expr.union_monostate:
                    fact = expr.operand.union_read
                    absent = self.result(BOOL, MIRIsAlternative(self.bindings[expr.operand.name],
                                                              (fact.elements.index(None),)), loc)
                    return self.result(BOOL, MIRNot(absent), loc) if expr.negate else absent
                present = self.result(BOOL, MIRIsPresent(self.bindings[expr.operand.name]), loc)
                return present if expr.negate else self.result(BOOL, MIRNot(present), loc)
            case th.THIRBinOp() if expr.op in ("&&", "||"):
                left = self.expr(expr.left)
                return self.select(left, expr.right if expr.op == "&&" else left,
                                   left if expr.op == "&&" else expr.right, BOOL, loc)
            case th.THIRBinOp() if expr.op in th.COMPARISON_OPS:
                left = self.binop_operand(expr, 0)
                return self.result(BOOL, MIRCompare(expr.op, left, self.binop_operand(expr, 1)), loc)
            case th.THIRBinOp() | th.THIRUnaryArith():
                return self.result(typ, self.operation(expr), loc)
            case th.THIRValueSelect():
                left = self.expr(expr.lhs)
                return self.select(left, expr.rhs if expr.op == "&&" else left,
                                   left if expr.op == "&&" else expr.rhs, BOOL, loc)
            case th.THIRIfExpr():
                return self.select(self.expr(expr.cond), expr.then, expr.orelse, typ, loc)
            case _:
                raise AssertionError("coverage and expression lowering disagree")

    def record_value(self, expr: th.THIRExpr) -> MIRRvalue:
        match expr:
            case th.THIRCtorCall():
                definition = self.records[expr.result_type]
                args = {p.name: self.constructor_argument(arg, p)
                        for p, arg in zip(definition.constructor.params, expr.args)}
                return self.construct(definition.initializers, args, expr.loc)
            case th.THIRCall() | th.THIRMethodCall():
                # The callee's owned result, handed over into the destination.
                self.initialize_temporaries(expr)
                return self.call(expr)
            case th.THIRMove():
                return MIRMove(self.storage[expr.value.name])
            case th.THIRCopy() | th.THIRFormConvert():
                return self.record_copy(expr, expr.value)
            # A name or member read into storage: C++ copies what it reads.
            case (th.THIRName() | th.THIRSelf()) if expr.form is th.Form.BORROW:
                return self.record_copy(expr, expr)
            case th.THIRFieldAccess() if expr.form is th.Form.STORAGE:
                return self.record_copy(expr, expr)
            case _:
                raise AssertionError("coverage and expression lowering disagree")

    def record_copy(self, expr: th.THIRExpr, source: th.THIRExpr) -> MIRCopy:
        place = self.record_source(source)
        typ = unwrap_readonly(unwrap_ref_type(expr.result_type))
        return MIRCopy(place, _copy_may_raise(self.records, typ) or reaches_element(place))

    def record_source(self, expr: th.THIRExpr) -> MIRPlace:
        """The place of a record source (`_Coverage.record_source`): the
        storage a name's holder reaches, or a member place."""
        if isinstance(expr, th.THIRFieldAccess):
            return self.place(expr)
        return MIRPlace(self.place(expr).root, (MIRDeref(),))

    def record_holder(self, expr: th.THIRExpr) -> MIRSlotId:
        """A holder of a record source for an operation that reads one (a
        lent argument, a copied element): a name's own holder, else a
        borrow of the member place at the access coverage checked."""
        if not isinstance(expr, th.THIRFieldAccess):
            return self.place(expr).root
        fact = self.record_sources[expr]
        holder = self.slot(fact.type, reference=fact)
        self.write(holder, MIRBorrow(self.place(expr), self.chain_raises(expr)), expr.loc)
        return holder

    def constructor_argument(self, arg: th.THIRExpr, param: th.THIRParam) -> MIRSlotId:
        """A construct's operand for one constructor parameter, in the form
        the parameter binds it: an owned leaf lent or handed over, a record
        handed over in a temporary, a record lent through a holder (a
        temporary bound to it borrowed), else a scalar value."""
        if owned_parameter(param):
            return self.owned_argument(arg, owned_value_type(param.type), param.passing)
        if owned_record_parameter(param) is not None:
            return self.record_temporary(arg)
        if record_parameter(param):
            if isinstance(arg, (th.THIRName, th.THIRSelf, th.THIRFieldAccess)):
                return self.record_holder(arg)
            storage = self.record_temporary(arg)
            typ = self.slots[storage.index].type
            holder = self.slot(typ, reference=th.THIRBorrowedRecord(typ, True))
            self.write(holder, MIRBorrow(MIRPlace(storage)), arg.loc)
            return holder
        if view_leaf(param.type):
            return self.view_slot(arg)
        return self.expr(arg)

    def construct(self, initializers: tuple[MIRFieldInitializer, ...], args: Mapping[str, MIRSlotId],
                  loc: SourceLocation | None) -> MIRConstruct:
        """The definition decided each member's source: a parameter's
        argument (copied out of a lent holder or moved out of the handed-over
        temporary), an inert constant, or a member built by its own
        constructor over the same arguments into storage of the full
        expression, then moved in. Only the member copies may raise; a
        composed member's own construct carries its copies."""
        fields = []
        for init in initializers:
            if isinstance(init.source, MIRComposedConstruct):
                assert self.region.index != 0
                member = self.slot(unwrap_readonly(init.field.type), storage=True, storage_duration=self.region)
                self.write(member, self.construct(init.source.initializers, args, loc), loc,
                           MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                fields.append(member)
            elif isinstance(init.source, str):
                fields.append(args[init.source])
            elif init.mode is MIRMemberInitMode.BORROW:
                # A literal a view member stores: a holder of its static storage.
                view = init.field.type
                holder = self.slot(view, reference=_view_holder_fact(view))
                self.write(holder, init.source, init.loc)
                fields.append(holder)
            else:
                fields.append(self.result(init.field.type, init.source, init.loc))
        return MIRConstruct(tuple(fields), any(init.may_raise for init in initializers
                                                 if not isinstance(init.source, MIRComposedConstruct)))

    def operand(self, expr: th.THIRExpr) -> MIRSlotId:
        """The slot an operation reads: a scalar value, or a borrowed holder
        of an owned leaf or a view that stays live until the operation runs."""
        typ = self.types[expr]
        if view_leaf(typ):
            return self.view_slot(expr)
        return self.owned_operand(expr) if owned_leaf(typ) else self.expr(expr)

    def view_slot(self, expr: th.THIRExpr) -> MIRSlotId:
        """A view holder of `expr`'s value: a view binding itself, else a new
        holder typed by the family's view, written from `view_rvalue`."""
        if (isinstance(expr, th.THIRName) and expr.global_binding is None and expr.name in self.bindings
                and view_leaf(self.slots[self.bindings[expr.name].index].type)):
            return self.bindings[expr.name]
        typ = self.types[expr]
        view = typ if view_leaf(typ) else view_family_of(typ).view_type
        holder = self.slot(view, reference=_view_holder_fact(view))
        self.write(holder, self.view_rvalue(expr), expr.loc)
        return holder

    def view_rvalue(self, expr: th.THIRExpr) -> MIRRvalue:
        """The value a view holder takes from `expr`: an alias of a view, a
        borrow of a name's or global's storage, a static literal, a call's
        view result, or an alias of the holder an owned operand builds. A
        slice reads its bounds first and borrows its whole receiver."""
        typ = self.types[expr]
        if expr in self.element_places:
            return MIRBorrow(self.element_place(expr), self.element_raises(expr))
        if not view_leaf(typ):
            match expr:
                case th.THIRName() | th.THIRModuleVar():
                    return MIRBorrow(self.owned_place(expr))
                case th.THIRStrLiteral() | th.THIRBytesLiteral(form=th.Form.BORROW):
                    return MIRConstant(expr.value)
                case th.THIRCoerce() if expr.owned_passthrough:
                    return self.view_rvalue(expr.expr)
            return MIRAlias(self.owned_operand(expr))
        match expr:
            case th.THIRName():
                return MIRAlias(self.bindings[expr.name])
            case th.THIRFieldAccess():
                return MIRBorrow(self.place(expr), self.chain_raises(expr))
            case th.THIRCoerce():
                return self.view_rvalue(expr.expr)
            case th.THIRStrSlice():
                for bound in (expr.lower, expr.upper):
                    if bound is not None:
                        self.expr(bound)
                return self.view_rvalue(expr.receiver)
            case _:
                assert _user_call(expr)
                self.initialize_temporaries(expr)
                return self.call(expr)

    def through(self, expr: th.THIRExpr) -> MIRPlace:
        """The storage an owning sink copies `expr` from: a name's own place,
        else what the holder of the lent value reaches."""
        if isinstance(expr, (th.THIRName, th.THIRModuleVar)) and owned_leaf(self.types[expr]):
            return self.owned_place(expr)
        return MIRPlace(self.operand(expr), (MIRDeref(),))

    def operation(self, expr: th.THIRBinOp | th.THIRUnaryArith) -> MIROp:
        # A primitive operation's contract admits raising.
        if isinstance(expr, th.THIRBinOp):
            left = self.binop_operand(expr, 0)
            return MIROp(expr.op, (left, self.binop_operand(expr, 1)), may_raise=True)
        return MIROp(expr.resolved.method.name, (self.operand(expr.operand),), may_raise=True)

    def binop_operand(self, expr: th.THIRBinOp, side: int) -> MIRSlotId:
        """The slot the operator reads for one operand; a promoted operand is
        first converted, like a coercion, into the operator's type."""
        operand = (expr.left, expr.right)[side]
        if expr.promoted_operand != side:
            return self.operand(operand)
        promotion = expr.resolved.promotion
        target = promotion.return_type
        value = MIROp(f"coerce:{promotion.name}", (self.operand(operand),), may_raise=True)
        if not owned_leaf(target):
            return self.result(target, value, operand.loc)
        # Coverage placed the promoted value inside its full expression's region.
        assert self.region.index != 0
        temporary = self.slot(target, storage=True, storage_duration=self.region)
        self.write(temporary, value, operand.loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return self.owned_holder(target, MIRBorrow(MIRPlace(temporary)), operand.loc)

    def conversion(self, expr: th.THIRCoerce) -> MIROp:
        return MIROp(f"coerce:{expr.coercion_name}", (self.operand(expr.expr),), may_raise=True)

    def owned_place(self, expr: th.THIRName | th.THIRModuleVar) -> MIRPlace:
        """The storage an owned-leaf name denotes: the body's own, or what a
        parameter or global handle borrows."""
        if expr.global_binding is not None:
            fact = expr.global_binding
            return MIRPlace(self.globals[MIRGlobalId(fact.module, fact.name)], (MIRDeref(),))
        sid = self.bindings[expr.name]
        return (MIRPlace(sid, (MIRDeref(),)) if self.slots[sid.index].value_kind is MIRValueKind.BORROWED
                else MIRPlace(sid))

    def owned_holder(self, typ: TpyType, value: MIRRvalue, loc: SourceLocation | None) -> MIRSlotId:
        holder = self.slot(typ, reference=th.THIRBorrowedRecord(typ, True))
        self.write(holder, value, loc)
        return holder

    def owned_operand(self, expr: th.THIRExpr) -> MIRSlotId:
        typ, loc = self.types[expr], expr.loc
        match expr:
            case th.THIRName() | th.THIRModuleVar():
                return self.owned_holder(typ, MIRBorrow(self.owned_place(expr)), loc)
            case th.THIRStrLiteral() | th.THIRBytesLiteral(form=th.Form.BORROW):
                return self.owned_holder(typ, MIRConstant(expr.value), loc)
            case th.THIRCall() | th.THIRMethodCall() if self.calls[expr].borrowed_result is not None:
                return self.borrowing_call(expr)
            case th.THIRCoerce() if expr.owned_passthrough:
                return self.view_slot(expr)
            case th.THIRFieldAccess():
                return self.owned_holder(typ, MIRBorrow(self.place(expr), self.chain_raises(expr)), loc)
            case th.THIRSubscript() if expr in self.element_places:
                return self.owned_holder(typ, MIRBorrow(self.element_place(expr), self.element_raises(expr)), loc)
        # Coverage placed every built operand inside a full expression's region.
        assert self.region.index != 0
        temporary = self.slot(typ, storage=True, storage_duration=self.region)
        self.write(temporary, self.owned_rvalue(expr), loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return self.owned_holder(typ, MIRBorrow(MIRPlace(temporary)), loc)

    def owned_rvalue(self, expr: th.THIRExpr) -> MIRRvalue:
        """The value an owned-leaf expression writes into owned storage."""
        typ = self.types[expr]
        match expr:
            case th.THIRName() | th.THIRModuleVar():
                return MIRCopy(self.owned_place(expr), may_raise=type_def_of(typ).copy_may_raise)
            case th.THIRFormConvert():
                return MIRCopy(self.through(expr.value), may_raise=type_def_of(typ).copy_may_raise)
            case th.THIRFieldAccess():
                place = self.place(expr)
                return MIRCopy(place, may_raise=bool(type_def_of(typ).copy_may_raise) or reaches_element(place))
            case th.THIRSubscript() if expr in self.element_places:
                # Copying an element also checks its index.
                return MIRCopy(self.element_place(expr), may_raise=True)
            case th.THIRStrLiteral() | th.THIRBytesLiteral() | th.THIRLiteral():
                return MIRConstant(expr.value)
            case th.THIRCoerce() if expr.owned_passthrough:
                return MIRCopy(self.through(expr.expr), may_raise=type_def_of(typ).copy_may_raise)
            case th.THIRCoerce():
                return self.conversion(expr)
            case th.THIRCall() | th.THIRMethodCall() if self.calls[expr].borrowed_result is not None:
                # C++ copies the result it was handed by reference into the sink.
                return MIRCopy(MIRPlace(self.borrowing_call(expr), (MIRDeref(),)),
                               may_raise=type_def_of(typ).copy_may_raise)
            case th.THIRCall() | th.THIRMethodCall():
                self.initialize_temporaries(expr)
                return self.call(expr)
            case _:
                return self.operation(expr)

    def borrowing_call(self, expr: th.THIRCall | th.THIRMethodCall) -> MIRSlotId:
        """A call whose owned-leaf result may borrow its lent arguments: a
        readonly holder of the result, whose referents are theirs."""
        self.initialize_temporaries(expr)
        return self.owned_holder(self.types[expr], self.call(expr), expr.loc)

    def owned_storage(self, typ: TpyType, kind: MIRSlotKind, name: str | None = None) -> MIRSlotId:
        return self.slot(typ, kind, name, storage=True,
                         storage_duration=self.region if self.region.index else MIRStorageDuration.BODY)

    def owned_initialization(self, dest: MIRSlotId) -> MIRRecordWrite:
        duration = self.slots[dest.index].storage_duration
        return MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION if isinstance(duration, MIRRegionId)
                              else MIRRecordWriteMode.INITIALIZE_ONCE)

    def owned_write(self, dest: MIRSlotId | MIRPlace, expr: th.THIRExpr, loc: SourceLocation | None, *,
                    replace: bool = False) -> None:
        fact = MIRRecordWrite(MIRRecordWriteMode.IN_PLACE) if replace else self.owned_initialization(dest)
        with self.full_expression(expr):
            self.write(dest, self.owned_rvalue(expr), loc, fact)

    def owned_return(self, expr: th.THIRExpr, loc: SourceLocation | None, storage: Mapping[str, MIRSlotId],
                     result_slot: Callable[[MIRStorageDuration | MIRRegionId], MIRSlotId],
                     rvalue: Callable[[th.THIRExpr], MIRRvalue]) -> None:
        """Return an owned record or container result (`Own[...]`): a local's
        own storage (`storage` by name) leaves the body itself -- the move is
        C++'s (or elided), not an event of the body; anything else is built
        into a result slot (`result_slot`) by `rvalue`."""
        if isinstance(expr, th.THIRName):
            self.end(MIRReturn(storage[expr.name], loc))
            return
        dest = result_slot(self.region if self.region.index else MIRStorageDuration.BODY)
        with self.full_expression(expr):
            self.write(dest, rvalue(expr), loc, self.owned_initialization(dest))
        self.end(MIRReturn(dest, loc))

    def owned_result(self, expr: th.THIRExpr) -> MIRSlotId:
        # The body's own storage is returned itself (C++ moves it out); anything
        # else is copied or built into the result.
        if isinstance(expr, th.THIRName) and expr.global_binding is None:
            sid = self.bindings[expr.name]
            if self.slots[sid.index].value_kind is MIRValueKind.OWNED:
                return sid
        dest = self.owned_storage(self.types[expr], MIRSlotKind.TEMPORARY)
        self.owned_write(dest, expr, expr.loc)
        return dest

    @contextmanager
    def full_expression(self, expr: th.THIRExpr | th.THIRPrint) -> Iterator[None]:
        if expr not in self.full_expressions:
            yield
            return
        entry, after = self.block(), self.block()
        self.end(MIRGoto(entry.id, expr.loc))
        self.current = entry
        with self.scope():
            yield
            self.end(MIRGoto(after.id, expr.loc))
        self.current = after

    def full_expression_value(self, expr: th.THIRExpr) -> MIRSlotId:
        if expr not in self.full_expressions:
            return self.expr(expr)
        result = self.slot(self.types[expr])
        with self.full_expression(expr):
            self.write(result, MIRRead(MIRPlace(self.expr(expr))), expr.loc)
        return result

    def initial_mode(self) -> MIRRecordWriteMode:
        return (MIRRecordWriteMode.INITIALIZE_ONCE if self.region.index == 0
                else MIRRecordWriteMode.INITIALIZE_REGION)

    def placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> MIRRegionId | MIRStorageDuration:
        if (isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                and stmt.storage_placement is th.THIRStoragePlacement.BODY):
            return MIRStorageDuration.BODY
        _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.SCOPE,
                 "missing direct storage placement")
        return self.region if self.region.index else MIRStorageDuration.BODY

    def optional_record(self, expr: th.THIRExpr, fact: th.THIRBorrowedRecord,
                        mode: MIRRecordWriteMode) -> MIRRvalue:
        storage = self.slot(fact.type, storage=True, storage_duration=(self.region
                            if mode is MIRRecordWriteMode.INITIALIZE_REGION else MIRStorageDuration.BODY))
        self.write(storage, self.record_value(expr), expr.loc, MIRRecordWrite(mode))
        reference = self.slot(fact.type, reference=fact)
        self.write(reference, MIRBorrow(MIRPlace(storage)), expr.loc)
        return MIROptionalConstruct(reference)

    def stmts(self, stmts: tuple[th.THIRStmt, ...]) -> None:
        for stmt in stmts:
            if self.current is None:
                # Coverage already inspected every retained unreachable node.
                break
            loc = stmt.loc
            if not isinstance(stmt, (th.THIRIf, th.THIRWhile)):
                self.declare_temporaries(stmt)
            match stmt:
                case th.THIRNoOpStmt():
                    continue
                case th.THIRVarDecl() if stmt.native_container is not None and stmt.form is not th.Form.BORROW:
                    typ = stmt.native_container.type
                    dest = self.slot(typ, MIRSlotKind.LOCAL, stmt.name, storage=True,
                                     storage_duration=self.region if self.region.index else MIRStorageDuration.BODY,
                                     layout=with_access(self.layouts[typ], False))
                    with self.full_expression(stmt.init):
                        self.write(dest, self.container_construct(stmt.init, typ), loc, self.owned_initialization(dest))
                    self.bindings[stmt.name] = dest
                case th.THIRVarDecl() if stmt.native_container is not None:
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name, container=stmt.native_container)
                    if _user_call(stmt.init):
                        # The alias binds the container the call returns by reference.
                        source = self.container_slot(stmt.init)
                        self.borrow_operation(stmt)
                        self.write(dest, MIRAlias(source), loc)
                    else:
                        self.write(dest, MIRAlias(self.bindings[stmt.init.name]) if isinstance(stmt.init, th.THIRName)
                                   else MIRBorrow(self.place(stmt.init)), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRVarDecl() if stmt in self.element_bindings:
                    fact = self.element_bindings[stmt]
                    holder = self.slot(fact.type, MIRSlotKind.LOCAL, stmt.name, fact)
                    with self.full_expression(stmt.init):
                        self.write(holder, MIRBorrow(self.element_place(stmt.init), self.element_raises(stmt.init)),
                                   loc)
                    self.bindings[stmt.name] = holder
                case th.THIRVarDecl() if stmt in self.span_holders:
                    typ = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
                    holder = self.container_view_slot(typ, self.span_holders[stmt], MIRSlotKind.LOCAL, stmt.name)
                    with self.full_expression(stmt.init):
                        self.write(holder, self.span_rvalue(stmt.init, holder), loc)
                    self.bindings[stmt.name] = holder
                case th.THIRSetItem():
                    self.element_write(stmt)
                case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.name in self.bindings
                                         and owned_container(self.slots[self.bindings[stmt.target.name].index])):
                    dest = self.bindings[stmt.target.name]
                    with self.full_expression(stmt.value):
                        self.write(dest, self.container_construct(stmt.value, self.slots[dest.index].type), loc,
                                   MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
                case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.name in self.bindings
                                         and container_view_holder(self.slots[self.bindings[stmt.target.name].index])):
                    holder = self.bindings[stmt.target.name]
                    with self.full_expression(stmt.value):
                        self.write(holder, self.span_rvalue(stmt.value, holder), loc)
                case th.THIRNarrowAlias():
                    fact = stmt.union_extraction
                    member = fact.layout.elements[fact.alternative]
                    source = self.union_place(fact)
                    reference = member if isinstance(member, th.THIRBorrowedRecord) else None
                    dest = self.slot(reference.type if reference else member, MIRSlotKind.LOCAL,
                                     stmt.alias, reference, alias_source=None if reference else source)
                    self.write(dest, MIRUnionExtract(source), loc)
                    self.bindings[stmt.alias] = dest
                case th.THIRVarDecl() if stmt.union_layout is not None:
                    dest = self.slot(stmt.union_layout.type, MIRSlotKind.LOCAL, stmt.name,
                                     union_layout=stmt.union_layout, storage_duration=(self.placement(stmt)
                                         if all(m is None or storage_leaf(m)
                                                for m in stmt.union_layout.elements) else None))
                    self.write(dest, self.union_value(stmt.init, stmt.union_layout, stmt.union_literal), loc,
                               self.payload_write(dest, MIRPayloadWriteMode.INITIALIZE))
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() if stmt.union_layout is not None:
                    dest = self.bindings[stmt.target.name]
                    self.write(dest, self.union_value(stmt.value, stmt.union_layout, stmt.union_literal), loc,
                               self.payload_write(dest, MIRPayloadWriteMode.ASSIGN))
                case th.THIRVarDecl() | th.THIRPtrLocalDecl() if stmt.optional_layout is not None:
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name,
                                     optional_layout=stmt.optional_layout, storage_duration=(self.placement(stmt)
                                         if storage_leaf(stmt.optional_layout.payload) else None))
                    if stmt.owned_storage is not None:
                        self.placement(stmt)
                    value = (self.optional_record(stmt.init, stmt.owned_storage, self.initial_mode())
                             if stmt.owned_storage is not None else self.optional_value(stmt.init, stmt.optional_layout))
                    self.write(dest, value, loc, self.payload_write(dest, MIRPayloadWriteMode.INITIALIZE))
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.optional_layout is not None:
                    name = stmt.target.name if isinstance(stmt, th.THIRAssign) else stmt.name
                    dest = self.bindings[name]
                    if isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is RebindStorage.OWN:
                        self.write(dest, self.optional_record(stmt.value, stmt.optional_layout.payload,
                                                             MIRRecordWriteMode.OWN_SITE), loc)
                    elif isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is RebindStorage.IN_PLACE:
                        self.write(MIRPlace(dest, (MIROptionalPayload(), MIRDeref())),
                                   self.record_value(stmt.value), loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, dest))
                    else:
                        self.write(dest, self.optional_value(stmt.value, stmt.optional_layout), loc,
                                   self.payload_write(dest, MIRPayloadWriteMode.ASSIGN))
                case th.THIRVarDecl() | th.THIRPtrLocalDecl() if stmt.owned_storage is not None:
                    fact = stmt.owned_storage
                    storage = self.slot(fact.type, storage=True, storage_duration=self.placement(stmt))
                    mode = (MIRRecordWriteMode.OWN_SITE if isinstance(stmt, th.THIRPtrLocalDecl)
                            and stmt.kind is th.PtrSlotKind.RECORD_HOISTED else self.initial_mode())
                    with self.full_expression(stmt.init):
                        self.write(storage, self.record_value(stmt.init), loc, MIRRecordWrite(mode))
                    holder = self.slot(fact.type, MIRSlotKind.LOCAL, stmt.name, fact)
                    self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                    self.storage[stmt.name] = storage
                    self.bindings[stmt.name] = holder
                case th.THIRAssign() if stmt.optional_record_assignment is not None:
                    name = stmt.target.name
                    storage = self.storage[name]
                    self.write(storage, self.record_value(stmt.value), loc,
                               MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
                    self.write(self.bindings[name], MIRBorrow(MIRPlace(storage)), loc)
                case th.THIRAssign() if stmt.rebind_storage is not None:
                    holder = self.bindings[stmt.target.name]
                    with self.full_expression(stmt.value):
                        value = self.record_value(stmt.value)
                        if stmt.rebind_storage is RebindStorage.OWN:
                            storage = self.slot(self.slots[holder.index].type, storage=True,
                                                storage_duration=MIRStorageDuration.BODY)
                            self.write(storage, value, loc, MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))
                            self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                        else:
                            self.write(MIRPlace(holder, (MIRDeref(),)), value, loc,
                                       MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder))
                case (th.THIRVarDecl() | th.THIRPtrLocalDecl() | th.THIRAssign()
                      | th.THIRPtrLocalRebind()) if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                    declaration = isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl))
                    if stmt.storage_borrow is not None:
                        fact = stmt.storage_borrow
                        source = stmt.init if declaration else stmt.value
                        if isinstance(source, th.THIRFormConvert):
                            source = source.value
                        value = MIRBorrow(self.place(source))
                        if isinstance(source, th.THIRSubscript):
                            value = MIRBorrow(MIRPlace(value.source.root, (*value.source.projections, MIRDeref())))
                    else:
                        fact = stmt.alias_binding.reference
                        value = MIRAlias(self.bindings[stmt.alias_binding.source])
                    name = stmt.target.name if isinstance(stmt, th.THIRAssign) else stmt.name
                    if declaration:
                        self.bindings[name] = self.slot(fact.type, MIRSlotKind.LOCAL, name, fact)
                    self.borrow_operation(stmt)
                    self.write(self.bindings[name], value, loc)
                case th.THIRVarDecl() if stmt.tuple_storage_alias is not None:
                    self.bindings[stmt.name] = self.bindings[stmt.tuple_storage_alias.source]
                case th.THIRVarDecl() | th.THIRPtrLocalDecl() | th.THIRPtrLocalRebind() if stmt in self.borrowed_bindings:
                    fact = self.borrowed_bindings[stmt]
                    declaration = isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl))
                    source = self.borrowed_expression(stmt.init if declaration else stmt.value, fact)
                    if declaration:
                        self.bindings[stmt.name] = self.slot(fact.type, MIRSlotKind.LOCAL, stmt.name, fact)
                    self.borrow_operation(stmt)
                    self.write(self.bindings[stmt.name], MIRAlias(source), loc)
                case th.THIRVarDecl() if stmt.tuple_layout is not None:
                    owning = stmt.tuple_layout.owns_records
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name,
                                     tuple_layout=stmt.tuple_layout,
                                     storage_duration=self.placement(stmt) if owning else None)
                    if owning:
                        value = MIRTupleConstruct(tuple(self.record_value(e) if isinstance(m, th.THIROwnedRecord)
                                                        else self.expr(e)
                                                        for e, m in zip(stmt.init.elements, stmt.tuple_layout.elements)))
                        self.write(dest, value, loc, MIRTupleInitialization())
                    else:
                        self.write(dest, MIRTupleCopy(self.tuple_expr(stmt.init)), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRVarDecl() if owned_leaf(stmt.resolved_type):
                    dest = self.owned_storage(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name)
                    self.owned_write(dest, stmt.init, loc)
                    self.bindings[stmt.name] = dest
                case th.THIRVarDecl() if view_leaf(stmt.resolved_type):
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name,
                                     _view_holder_fact(stmt.resolved_type))
                    with self.full_expression(stmt.init):
                        self.write(dest, self.view_rvalue(stmt.init), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() if (isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is None
                                         and stmt.target.name in self.bindings
                                         and view_leaf(self.slots[self.bindings[stmt.target.name].index].type)):
                    with self.full_expression(stmt.value):
                        self.write(self.bindings[stmt.target.name], self.view_rvalue(stmt.value), loc)
                case th.THIRVarDecl():
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name)
                    if stmt.init is not None:
                        with self.full_expression(stmt.init):
                            self.write(dest, MIRRead(MIRPlace(self.expr(stmt.init))), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() if isinstance(stmt.target, th.THIRName) and owned_leaf(stmt.target.result_type):
                    self.owned_write(self.bindings[stmt.target.name], stmt.value, loc, replace=True)
                case th.THIRAssign() if isinstance(stmt.target, th.THIRFieldAccess) and owned_leaf(stmt.target.result_type):
                    self.owned_write(self.place(stmt.target), stmt.value, loc, replace=True)
                case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess) and record_type(
                        unwrap_readonly(unwrap_ref_type(stmt.target.result_type)))):
                    target = self.place(stmt.target)
                    with self.full_expression(stmt.value):
                        self.write(target, self.record_value(stmt.value), loc,
                                   MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
                case th.THIRAssign() if (isinstance(stmt.target, th.THIRFieldAccess)
                                         and view_leaf(stmt.target.result_type)):
                    target = self.place(stmt.target)
                    with self.full_expression(stmt.value):
                        self.write(target, MIRAlias(self.view_slot(stmt.value)), loc)
                case th.THIRAssign():
                    if isinstance(stmt.target.result_type, TupleType):
                        self.write(self.place(stmt.target), MIRTupleCopy(self.tuple_expr(stmt.value)), loc)
                    else:
                        with self.full_expression(stmt.value):
                            self.write(self.place(stmt.target), MIRRead(MIRPlace(self.expr(stmt.value))), loc)
                case th.THIRExprStmt():
                    with self.full_expression(stmt.expr):
                        if th.record_rvalue_storage(stmt.expr) is not None:
                            self.place(stmt.expr)
                        elif (isinstance(stmt.expr, (th.THIRCall, th.THIRMethodCall))
                              and isinstance(stmt.expr.result_type, VoidType)):
                            self.initialize_temporaries(stmt.expr)
                            self.current.statements.append(MIRCallStmt(self.call(stmt.expr), loc))
                        else:
                            self.expr(stmt.expr)
                case th.THIRReturn() if (stmt.value is not None and self.borrowed_result is not None
                                         and view_leaf(self.borrowed_result.type)):
                    # The result holder outlives the full expression, whose
                    # temporaries a returned view must not reach.
                    view = self.borrowed_result.type
                    holder = self.slot(view, reference=_view_holder_fact(view))
                    with self.full_expression(stmt.value):
                        self.write(holder, self.view_rvalue(stmt.value), loc)
                    self.end(MIRReturn(holder, loc))
                case th.THIRReturn() if (stmt.value is not None and self.borrowed_result is not None
                                         and _region_view(self.borrowed_result.type)):
                    result = self.borrowed_result
                    holder = self.container_view_slot(result.type, result.readonly)
                    with self.full_expression(stmt.value):
                        self.write(holder, self.span_rvalue(stmt.value, holder), loc)
                    self.end(MIRReturn(holder, loc))
                case th.THIRReturn() if stmt.value is not None and self.container_result is not None:
                    self.end(MIRReturn(self.container_slot(stmt.value), loc))
                case th.THIRReturn() if stmt.value is not None and self.owned_container_result is not None:
                    typ = self.owned_container_result
                    self.owned_return(stmt.value, loc, self.bindings, lambda duration: self.slot(
                        typ, storage=True, layout=with_access(self.layouts[typ], False), storage_duration=duration),
                        lambda value: self.container_construct(value, typ))
                case th.THIRReturn() if stmt.value is not None and self.owned_record_result is not None:
                    typ = self.owned_record_result
                    self.owned_return(stmt.value, loc, self.storage,
                                      lambda duration: self.slot(typ, storage=True, storage_duration=duration),
                                      self.record_value)
                case th.THIRReturn():
                    result = self.borrowed_result
                    value = (None if stmt.value is None else self.borrowed_expression(stmt.value, result)
                             if result is not None else self.owned_result(stmt.value)
                             if owned_value_type(self.fn.return_type) is not None
                             else self.full_expression_value(stmt.value))
                    if result is not None:
                        self.borrow_operation(stmt)
                    self.end(MIRReturn(value, loc))
                case th.THIRIf():
                    if self.temp_plan is not None:
                        self.planned_if(stmt)
                        continue
                    self.hoists(stmt)
                    cond = self.full_expression_value(stmt.condition)
                    yes, no = self.block(), self.block()
                    self.branch(cond, yes.id, no.id, loc)
                    exits = []
                    for block, arm in ((yes, stmt.then_body), (no, stmt.else_body)):
                        self.current = block
                        self.scoped(arm)
                        if self.current is not None:
                            exits.append(self.current)
                    if exits:
                        join = self.block()
                        for block in exits:
                            self.current = block
                            self.end(MIRGoto(join.id, loc))
                        self.current = join
                    else:
                        self.current = None
                case th.THIRWhile():
                    if self.temp_plan is not None and self.temp_plan.scope(stmt, "iteration") is not None:
                        self.planned_while(stmt)
                        continue
                    self.hoists(stmt)
                    cond_block, body, normal, after = self.block(), self.block(), self.block(), self.block()
                    self.end(MIRGoto(cond_block.id, loc))
                    self.current = cond_block
                    self.branch(self.full_expression_value(stmt.condition), body.id, normal.id, loc)
                    self.current = body
                    self.loops.append((cond_block.id, after.id))
                    self.scoped(stmt.body, stmt, "loop")
                    self.loops.pop()
                    if self.current is not None:
                        self.end(MIRGoto(cond_block.id, loc))
                    self.current = normal
                    self.scoped(stmt.orelse, stmt if stmt.orelse else None, "else")
                    if self.current is not None:
                        self.end(MIRGoto(after.id, loc))
                    self.current = after
                case th.THIRForRange():
                    self.range_loop(stmt)
                case th.THIRForEach():
                    self.native_loop(stmt)
                case th.THIRPrint():
                    with self.full_expression(stmt):
                        arguments = tuple(self.operand(arg.expr) for arg in stmt.args)
                        self.current.statements.append(MIRPrint(arguments, loc))
                case th.THIRParamCopy():
                    param = self.bindings[stmt.name]
                    typ = self.slots[param.index].type
                    local = self.owned_storage(typ, MIRSlotKind.LOCAL, stmt.name)
                    self.write(local, MIRCopy(MIRPlace(param, (MIRDeref(),)), may_raise=type_def_of(typ).copy_may_raise),
                               loc, self.owned_initialization(local))
                    self.bindings[stmt.name] = local
                case th.THIRStrAppend():
                    # `t += v` appends in place: it reads the old value and v, then replaces t.
                    if stmt.target_expr is not None:
                        target, typ = self.place(stmt.target_expr), stmt.target_expr.result_type
                    else:
                        target = MIRPlace(self.bindings[stmt.target])
                        typ = self.slots[target.root.index].type
                    with self.full_expression(stmt.value):
                        value = self.operand(stmt.value)
                        current = self.owned_holder(typ, MIRBorrow(target), loc)
                        self.write(target, MIROp("+=", (current, value), may_raise=True), loc,
                                   MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
                case th.THIRBreak():
                    self.end(MIRGoto(self.loops[-1][1], loc))
                case th.THIRContinue():
                    self.end(MIRGoto(self.loops[-1][0], loc))
                case _:
                    raise AssertionError("coverage and statement lowering disagree")

    def container_construct(self, expr: th.THIRContainerLiteral, typ: NominalType) -> MIRConstruct:
        """A container literal: one operand per element (a dict's keys and
        values alternating), each in its member's form -- an inert leaf by
        value, an owned leaf through a holder its slot copies, a record
        built into a temporary it moves from or copied through a holder."""
        layout = self.layouts[typ]
        members = (layout.element,) if layout.value is None else (layout.element, layout.value)
        operands = (expr.elements if layout.value is None
                    else tuple(e for pair in zip(expr.elements, expr.values) for e in pair))
        fields = []
        for index, operand in enumerate(operands):
            member = members[index % len(members)]
            match member.kind:
                case MIRValueKind.SCALAR:
                    fields.append(self.expr(operand))
                case MIRValueKind.OWNED:
                    fields.append(self.operand(operand))
                case _:
                    fields.append(self.record_holder(operand.value) if isinstance(operand, th.THIRCopy)
                                  else self.record_temporary(operand))
        return MIRConstruct(tuple(fields), may_raise=True)

    def record_temporary(self, expr: th.THIRExpr) -> MIRSlotId:
        """A record built, handed over by a call, copied or moved into storage
        of the full expression for an operation that moves it out (a
        container element, an owning parameter) or lends it.
        THIR publishes no backing for it: it is MIR's own temporary."""
        storage = self.slot(unwrap_readonly(unwrap_ref_type(expr.result_type)), storage=True,
                            storage_duration=self.region)
        self.write(storage, self.record_value(expr), expr.loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return storage

    def element_write(self, stmt: th.THIRSetItem) -> None:
        """`xs[i] = v`: the value in the member's form (Python evaluates it
        first), then the index, replaces the element region in place under
        the container's root."""
        target = stmt.target
        if stmt in self.calls:
            # A `__setitem__` that may move elements: the stub call, writing the receiver's structure.
            summary = self.calls[stmt]
            with self.full_expression(stmt.value):
                self.initialize_temporaries(stmt.value)
                arguments = tuple(self.argument(arg, binding) for arg, binding
                                  in zip((target.receiver, target.index, stmt.value), summary.parameters))
                self.current.statements.append(
                    MIRCallStmt(MIRCall(summary, arguments, not summary.normal_return_only), stmt.loc))
            return
        with self.full_expression(stmt.value):
            member = self.element_places[target].layout.subscript
            match member.kind:
                case MIRValueKind.SCALAR:
                    value = MIRRead(MIRPlace(self.expr(stmt.value)))
                case MIRValueKind.OWNED:
                    value = self.owned_rvalue(stmt.value)
                case _:
                    value = self.record_value(stmt.value)
            place = self.element_place(target)
            self.write(place, value, stmt.loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, place.root))

    def planned_if(self, stmt: th.THIRIf) -> None:
        self.hoists(stmt)
        chain = if_chain(stmt)
        join = self.block()

        def arm(index: int) -> None:
            node = chain[index]
            self.declare_temporaries(node)
            condition = self.full_expression_value(node.condition)
            yes, no = self.block(), self.block()
            self.branch(condition, yes.id, no.id, node.loc)
            self.current = yes
            self.scoped(node.then_body, node, "then")
            if self.current is not None:
                self.end(MIRGoto(join.id, node.loc))
            self.current = no
            if index + 1 < len(chain):
                next_node = chain[index + 1]
                if self.temp_plan.scope(next_node, "condition") is not None:
                    with self.scope(next_node, "condition"):
                        arm(index + 1)
                else:
                    arm(index + 1)
            else:
                if node.else_body:
                    self.scoped(node.else_body, node, "else")
                if self.current is not None:
                    self.end(MIRGoto(join.id, node.loc))

        arm(0)
        self.current = join

    def planned_while(self, stmt: th.THIRWhile) -> None:
        self.hoists(stmt)
        bridge, condition, normal, after = self.block(), self.block(), self.block(), self.block()
        self.end(MIRGoto(bridge.id, stmt.loc))
        self.current = bridge
        self.end(MIRGoto(condition.id, stmt.loc))
        self.current = condition
        with self.scope(stmt, "iteration"):
            self.declare_temporaries(stmt)
            value = self.full_expression_value(stmt.condition)
            body = self.block()
            self.branch(value, body.id, normal.id, stmt.loc)
            self.current = body
            self.loops.append((bridge.id, after.id))
            self.stmts(stmt.body)
            self.loops.pop()
            if self.current is not None:
                self.end(MIRGoto(bridge.id, stmt.loc))
        self.current = normal
        if stmt.orelse:
            self.scoped(stmt.orelse, stmt, "else")
        if self.current is not None:
            self.end(MIRGoto(after.id, stmt.loc))
        self.current = after

    def range_loop(self, stmt: th.THIRForRange) -> None:
        loc = stmt.loc
        self.hoists(stmt)
        start = self.expr(stmt.start) if stmt.start is not None else self.result(INT32, MIRConstant(0), loc)
        stop = self.expr(stmt.stop)
        entry, normal, after = self.block(), self.block(), self.block()
        self.end(MIRGoto(entry.id, loc))
        self.current = entry
        with self.scope(stmt, "counter"):
            direct = not (stmt.hoist_loop_var or stmt.target_written)
            counter = self.slot(INT32, MIRSlotKind.LOCAL, stmt.var if direct else None)
            self.write(counter, MIRRead(MIRPlace(start)), loc)
            if direct:
                self.bindings[stmt.var] = counter
            head, body, advance = self.block(), self.block(), self.block()
            self.end(MIRGoto(head.id, loc))
            self.current = head
            step = 1 if stmt.step_kind == "plus_one" else -1
            cond = self.result(BOOL, MIRCompare("<" if step == 1 else ">", counter, stop), loc)
            self.branch(cond, body.id, normal.id, loc)
            self.current = body
            self.loops.append((advance.id, after.id))
            with self.scope(stmt, "loop"):
                if not direct:
                    if not stmt.hoist_loop_var:
                        self.bindings[stmt.var] = self.slot(INT32, MIRSlotKind.LOCAL, stmt.var)
                    self.write(self.bindings[stmt.var], MIRRead(MIRPlace(counter)), loc)
                self.stmts(stmt.body)
            self.loops.pop()
            if self.current is not None:
                self.end(MIRGoto(advance.id, loc))
            self.current = advance
            self.write(counter, MIRRangeAdvance(counter, step), loc)
            self.end(MIRGoto(head.id, loc))
        self.current = normal
        self.scoped(stmt.orelse, stmt if stmt.orelse else None, "else")
        if self.current is not None:
            self.end(MIRGoto(after.id, loc))
        self.current = after

    def native_loop(self, stmt: th.THIRForEach) -> None:
        loc = stmt.loc
        self.hoists(stmt)
        fact = stmt.iteration.source
        source = self.container_slot(stmt.iterable)
        holder = self.slots[source.index]
        iterator = self.slot(holder.type, container=th.THIRNativeContainer(holder.type, fact.element, holder.readonly),
                             iterator=True)
        self.write(iterator, MIRIteratorInit(source), loc)
        head, body, advance, normal, after = (self.block() for _ in range(5))
        self.end(MIRGoto(head.id, loc))
        self.current = head
        self.branch(self.result(BOOL, MIRIteratorHasNext(iterator), loc), body.id, normal.id, loc)
        self.current = body
        self.loops.append((advance.id, after.id))
        with self.scope(stmt, "loop"):
            if not stmt.hoist_loop_var:
                member = self.slots[iterator.index].container_layout.element
                reference = (fact.element if isinstance(fact.element, th.THIRBorrowedRecord)
                             # An owned-leaf element is held through a view of it or a readonly borrow.
                             else _view_holder_fact(stmt.elem_type) if view_leaf(stmt.elem_type)
                             else th.THIRBorrowedRecord(member.type, True) if member.kind is MIRValueKind.OWNED
                             else None)
                self.bindings[stmt.var] = self.slot(reference.type if reference else stmt.elem_type,
                                                    MIRSlotKind.LOCAL, stmt.var, reference)
            self.write(self.bindings[stmt.var], MIRIteratorRead(iterator), loc)
            self.stmts(stmt.body)
        self.loops.pop()
        if self.current is not None:
            self.end(MIRGoto(advance.id, loc))
        self.current = advance
        self.write(iterator, MIRIteratorAdvance(iterator), loc)
        self.end(MIRGoto(head.id, loc))
        self.current = normal
        self.scoped(stmt.orelse, stmt if stmt.orelse else None, "else")
        if self.current is not None:
            self.end(MIRGoto(after.id, loc))
        self.current = after

    def hoists(self, stmt: th.THIRIf | th.THIRWhile | th.THIRForRange | th.THIRForEach) -> None:
        for fact in stmt.hoisted_bindings:
            if fact.optional_record_storage is not None:
                reference = fact.optional_record_storage
                storage = self.slot(reference.type, MIRSlotKind.LOCAL, storage=True,
                                    storage_duration=self.region if self.region.index else MIRStorageDuration.BODY,
                                    record_storage=MIRRecordStorageKind.OPTIONAL)
                self.current.statements.append(MIRRecordStorageInit(MIRPlace(storage), stmt.loc))
                self.storage[fact.name] = storage
                self.bindings[fact.name] = self.slot(reference.type, MIRSlotKind.LOCAL, fact.name, reference)
                continue
            default = fact.physical_default
            dest = self.slot(
                fact.type, MIRSlotKind.LOCAL, fact.name, fact.borrowed_record,
                optional_layout=fact.optional_layout, tuple_layout=fact.tuple_layout,
                union_layout=fact.union_layout, storage_duration=(
                    self.region if self.region.index else MIRStorageDuration.BODY) if default is not None else None)
            self.bindings[fact.name] = dest
            if default is not None:
                self.current.statements.append(MIRStorageInit(MIRPlace(dest), default.alternative,
                                                              MIRConstant(default.value), stmt.loc))

    @contextmanager
    def scope(self, owner: th.THIRStmt | None = None, role: str = "") -> Iterator[None]:
        bindings, storage, region = self.bindings.copy(), self.storage.copy(), self.region
        temp_scope = self.temp_scope
        assert self.current is not None and not self.current.statements
        self.region = MIRRegionId(self.body, len(self.regions))
        self.regions.append(MIRRegion(self.region, region, self.current.id))
        self.current.region = self.region
        if self.temp_plan is not None and owner is not None:
            index = self.temp_plan.scope(owner, role)
            assert index is not None and self.temp_regions[self.temp_plan.scopes[index].parent] == region
            self.temp_regions[index] = self.region
            self.temp_scope = index
        try:
            yield
        finally:
            self.bindings, self.storage, self.region = bindings, storage, region
            self.temp_scope = temp_scope

    def scoped(self, stmts: tuple[th.THIRStmt, ...], owner: th.THIRStmt | None = None, role: str = "") -> None:
        with self.scope(owner, role):
            self.stmts(stmts)

    def build(self, initialization: MIRConstructorDefinition | None = None) -> MIRFunction:
        if self.fn.receiver is not None:
            # A constructor's receiver is the object it builds, passed by no caller.
            passing = th.receiver_param(self.fn.receiver).passing if initialization is None else None
            self.bindings["self"] = self.slot(self.fn.receiver.type, MIRSlotKind.PARAMETER,
                                              "self", self.fn.receiver, passing=passing)
        for p in self.fn.params:
            if (owned := owned_value_type(p.type)) is not None:
                self.bindings[p.name] = (
                    self.slot(owned, MIRSlotKind.PARAMETER, p.name, th.THIRBorrowedRecord(owned, True),
                              passing=p.passing) if p.passing in BORROWING_PASSINGS else
                    self.slot(owned, MIRSlotKind.PARAMETER, p.name, storage=True,
                              storage_duration=MIRStorageDuration.BODY, passing=p.passing))
                continue
            if view_leaf(p.type):
                self.bindings[p.name] = self.slot(p.type, MIRSlotKind.PARAMETER, p.name, _view_holder_fact(p.type),
                                                  passing=p.passing)
                continue
            if _region_view(p.type):
                # The caller's loan of a region, held by value.
                self.bindings[p.name] = self.container_view_slot(unwrap_readonly(unwrap_ref_type(p.type)), False,
                                                         MIRSlotKind.PARAMETER, p.name, p.passing)
                continue
            if p.native_container is not None and p.passing is ParamPassing.OWN:
                typ = p.native_container.type
                self.bindings[p.name] = self.slot(typ, MIRSlotKind.PARAMETER, p.name, storage=True,
                                                  storage_duration=MIRStorageDuration.BODY, passing=p.passing,
                                                  layout=with_access(self.layouts[typ], False))
                continue
            if (record := owned_record_parameter(p)) is not None:
                # The caller materialized the record before entry; the body
                # owns it and reaches it through a holder borrowed at entry.
                storage = self.slot(record, MIRSlotKind.PARAMETER, p.name, storage=True,
                                    storage_duration=MIRStorageDuration.BODY, passing=p.passing)
                holder = self.slot(record, MIRSlotKind.LOCAL, reference=th.THIRBorrowedRecord(record, False))
                self.write(holder, MIRBorrow(MIRPlace(storage)), None)
                self.storage[p.name] = storage
                self.bindings[p.name] = holder
                continue
            self.bindings[p.name] = self.slot(p.type, MIRSlotKind.PARAMETER, p.name,
                                               p.borrowed_record, passing=p.passing,
                                               optional_layout=p.optional_layout,
                                               container=p.native_container,
                                               union_layout=p.union_layout, tuple_layout=p.tuple_layout,
                                               storage_duration=MIRStorageDuration.CALLER
                                               if p.union_layout is not None and all(
                                                   member is None or storage_leaf(member)
                                                   for member in p.union_layout.elements) else None)
        for identity, fact in self.global_facts.items():
            # An owned-leaf global is read through a readonly handle on its external storage.
            reference = th.THIRBorrowedRecord(fact.type, True) if owned_leaf(fact.type) else None
            self.globals[identity] = self.slot(fact.type, MIRSlotKind.GLOBAL, fact.name, reference,
                                               global_binding=fact)
        receiver_init = None
        if initialization is not None:
            receiver_init = MIRReceiverInit(self.bindings["self"], tuple(
                self.member_init(init) for init in initialization.initializers))
        self.stmts(self.fn.body)
        reachable: set[MIRBlockId] = set()
        pending = [self.blocks[0].id]
        while pending:
            bid = pending.pop()
            if bid in reachable:
                continue
            reachable.add(bid)
            term = self.blocks[bid.index].terminator
            if term is not None:
                pending.extend(successors(term))
        if self.current is not None and self.current.id in reachable:
            _require(self.fn, isinstance(self.fn.return_type, VoidType), "non-void fallthrough")
            self.end(MIRReturn())
        blocks = []
        for b in self.blocks:
            if b.id not in reachable:
                continue
            assert b.terminator is not None
            blocks.append(MIRBlock(b.id, tuple(b.statements), b.terminator, b.region))
        kind = MIRBodyKind.CONSTRUCTOR if initialization is not None else function_body_kind(self.fn)
        slots = {s.id: s for s in self.slots}
        fn = MIRFunction(self.body, self.fn.return_type, self.reachable_slots(blocks, receiver_init), tuple(blocks),
                         self.blocks[0].id, tuple(d.layout for d in self.records.values()), receiver_init, kind,
                         tuple(r for r in self.regions if r.entry in reachable),
                         tuple({s.callee.identity: s for s in self.calls.values()}.values()),
                         self.borrowed_result,
                         body_may_raise(blocks, slots, receiver_init))
        validate_function(fn)
        return fn

    def member_init(self, init: MIRFieldInitializer) -> MIRMemberInit:
        """One member's entry initialization from its verified initializer:
        a parameter's slot (an `Own[R]` parameter's storage, not the holder
        the body reaches it through), a constant, a container literal built
        over parameters, or a record member built by its own constructor,
        initialized field by field like the receiver."""
        match init.source:
            case str():
                source = self.storage.get(init.source, self.bindings[init.source])
            case tuple():
                source = MIRConstruct(tuple(self.bindings[name] for name in init.source), True)
            case MIRComposedConstruct():
                source = MIRMemberInits(tuple(self.member_init(member) for member in init.source.initializers))
            case _:
                source = init.source
        return MIRMemberInit(source, init.mode, init.may_raise, init.loc)

    def reachable_slots(self, blocks: list[MIRBlock], receiver: MIRReceiverInit | None) -> tuple[MIRSlot, ...]:
        retained = {s.id for s in self.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
        if receiver is not None:
            retained.add(receiver.receiver)
            retained.update(member_init_operands(receiver.fields))
        for block in blocks:
            for stmt in block.statements:
                if (target := statement_target(stmt)) is not None:
                    retained.add(target.root)
                retained.update(statement_reads(stmt))
                if (isinstance(stmt, MIRAssign) and isinstance(stmt.storage_write, MIRRecordWrite)
                        and stmt.storage_write.rebind_owner is not None):
                    retained.add(stmt.storage_write.rebind_owner)
            match block.terminator:
                case MIRBranch(condition=condition):
                    retained.add(condition)
                case MIRReturn(value=value) if value is not None:
                    retained.add(value)
        pending = list(retained)
        while pending:
            source = self.slots[pending.pop().index].alias_source
            if source is not None and source.root not in retained:
                retained.add(source.root)
                pending.append(source.root)
        # Preserve IDs: consumers key by identity, and dead branches can leave gaps.
        return tuple(s for s in self.slots if s.id in retained)


@dataclass(frozen=True, eq=False)
class MIRLoweredStorage:
    function: MIRFunction
    # Captured when the builder allocates storage, never reconstructed from IDs.
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


def _build_storage(body: MIRBodyId, fn: th.THIRFunction, coverage: _Coverage,
                   initialization: MIRConstructorDefinition | None = None) -> MIRLoweredStorage:
    coverage.close_layouts()
    builder = _Builder(body, fn, coverage)
    try:
        function = builder.build(initialization)
    except MIRRepeatedInitializationError as failure:
        raise MIRUnsupported(failure, str(failure)) from failure
    except (MIRPresenceError, MIRDefiniteAssignmentError) as failure:
        raise MIRUnsupported(fn, str(failure)) from failure
    return MIRLoweredStorage(function, MappingProxyType(builder.backing_places),
                             MappingProxyType(builder.borrow_operations))


def lower_function_storage(fn: th.THIRFunction, body: MIRBodyId, *,
                           definitions: MIRDefinitions | None = None,
                           summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult] | None = None
                           ) -> MIRLoweredStorage | MIRNotCovered:
    """The ordinary lowering pass, retaining its actual backing correspondence."""
    try:
        # Resumable, generic and closure bodies never reach this pass: the
        # collector and the workspace refuse them before scheduling a lowering.
        coverage = _Coverage(fn, definitions if definitions is not None else MIRDefinitions(), summaries)
        coverage.check()
        return _build_storage(body, fn, coverage)
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))


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
        fn = th.THIRFunction(
            f"{ctor.record_name}.__init__", ctor.params, VoidType(), ctor.body, th.THIRFunctionLayout(),
            receiver=th.THIRBorrowedRecord(initialization.layout.type, False), temp_plan=ctor.temp_plan)
        coverage = _Coverage(fn, definitions, summaries)
        # The receiver under construction has the body-side layout, whose
        # definition a caller may not construct through.
        coverage.records[initialization.layout.type] = initialization
        coverage.check()
        # The receiver's entry initialization builds every container member,
        # a composed member's included.
        pending = list(initialization.initializers)
        while pending:
            init = pending.pop()
            if isinstance(init.source, MIRComposedConstruct):
                pending.extend(init.source.initializers)
            elif native_container_type(init.field.type):
                coverage.container_layout(ctor, init.field.type)
        return _build_storage(body, fn, coverage, initialization)
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))
