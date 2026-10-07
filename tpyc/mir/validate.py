"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from ..identity_map import IdentitySet
from ..parse import SourceLocation
from ..thir.nodes import COMPARISON_OPS, Form, THIRBorrowedRecord, THIRStubCallee, THIRStubContract
from ..type_def_registry import is_array
from ..thir.scalar_leaves import (
    binds_cursor, container_view, declared_members, holds_loan, leaf_constant, modeled_field, native_container_type,
    owned_constant, owned_leaf,
    owned_value_type, primitive_leaf, readonly_elements, record_type, storage_leaf, view_leaf,
)
from ..type_def_registry import ParamPassing, type_def_of, zero_value_of
from ..typesys import (
    BOOL, INT32, NominalType, OptionalType, ReadonlyType, Representation, TupleType, TpyType, VoidType,
    UnionType, certified_primitive_comparison, is_void_like_type, return_representation, through_view, unwrap_own,
    unwrap_readonly, unwrap_ref_type, view_family_of,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFieldId, MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRRvalue, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRReceiverInit, MIRBodyKind, MIRGlobalId,
    MIRMemberInit, MIRMemberInitMode, MIRMemberInits,
    MIRRecordWrite, MIRRecordWriteMode, MIRPayloadWrite, MIRPayloadWriteMode, MIRStorageInit, MIRRecordStorageInit, MIRStatement,
    MIRRegionId, MIREdge, MIRRecordStorageKind, MIRTupleInitialization,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRUnionLayout, MIRUnionPayload, MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract,
    MIRContainerLayout, MIRContainerElements, MIRContainerStructure, MIRRecordLayout,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance, MIROp, MIRPrint,
    statement_target,
)
from .presence import MIRPresence, _analyze_presence
from .coverage import (
    container_view_holder, handed_record, leaf_borrow, owned_borrow, owned_container, owned_storage, owned_tuple,
    primitive_operand, read_leaf, region_holder, scalar_member, scalar_slot, scalar_wrapper, view_compatible,
    view_holder,
)
from .region_flow import MIRRegionFlow, outgoing_edges
from .call_contract import (
    BORROWING_PASSINGS, OWNING_PASSINGS, binds_at, owned_record_result, path_hops, result_problem, summary_problem,
)
from .definitions import layout_copy_may_raise, with_access


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


def _expected_member(arg: TpyType, leaves_only: bool, readonly: bool,
                     records: 'Mapping[TpyType, MIRRecordLayout]') -> MIRTupleElement | None:
    bare = unwrap_readonly(arg)
    access = readonly or isinstance(arg, ReadonlyType)
    if storage_leaf(bare):
        return MIRTupleElement(bare)
    record = records.get(bare)
    if owned_leaf(bare):
        return MIRTupleElement(bare, MIRValueKind.OWNED, access) if record is not None and record.opaque else None
    # Whether the record dispatches user code (a comparison dunder) is the
    # lowering's admission (`plain_record_element`), decided under the
    # compilation; the shape is re-checked here.
    if (leaves_only or record is None or record.opaque
            or not all(storage_leaf(f.type) or owned_leaf(f.type) for f in record.fields)):
        return None
    return MIRTupleElement(bare, MIRValueKind.BORROWED, access)


def expected_container_layout(typ: TpyType, readonly: bool,
                              records: 'Mapping[TpyType, MIRRecordLayout]') -> MIRContainerLayout | None:
    """The layout a holder of `typ` with this access carries, re-derived from
    the type and the body's record layouts; None when `typ` is no admitted
    container or view of one."""
    members = declared_members(typ)
    if members is None:
        return None
    element, value, _ = members
    first = _expected_member(element, value is not None, readonly, records)
    second = None if value is None else _expected_member(value, True, readonly, records)
    if first is None or value is not None and second is None:
        return None
    return MIRContainerLayout(first, second)


def elements_member_type(place: MIRPlace, slots: 'Mapping[MIRSlotId, MIRSlot]') -> TpyType | None:
    """The member type a place ending in `[elements]` denotes: the root's
    subscript member, or that of the container field it projects. Shape
    only; `place_info` checks the projections."""
    if not place.projections or not isinstance(place.projections[-1], MIRContainerElements):
        return None
    root = slots.get(place.root)
    if root is None:
        return None
    if len(place.projections) == 1:
        return root.container_layout.subscript.type if root.container_layout is not None else None
    match place.projections[-2]:
        case MIRField(type=typ) if (members := declared_members(unwrap_readonly(typ))) is not None:
            element, value, _ = members
            return unwrap_readonly(value if value is not None else element)
    return None


def elements_place(place: MIRPlace) -> bool:
    return bool(place.projections) and isinstance(place.projections[-1], MIRContainerElements)


def reaches_element(place: MIRPlace) -> bool:
    """Whether a place passes through a container's element region: an
    access of it checks an index or key, so it may raise."""
    return any(isinstance(p, MIRContainerElements) for p in place.projections)


class MIRPresenceError(MIRValidationError):
    """A payload access lacks a valid presence proof at its CFG position."""


class MIRDefiniteAssignmentError(MIRValidationError):
    """Structural CFG paths do not establish initialization before a read."""


class MIRRepeatedInitializationError(MIRValidationError):
    """A static place initializes twice within one modeled region activation."""

    def __init__(self, loc: SourceLocation | None) -> None:
        super().__init__("repeated initialization within region activation")
        self.loc = loc


@dataclass(frozen=True)
class MIRPrepared:
    function: MIRFunction
    presence: MIRPresence


def _prepare_function(fn: MIRFunction) -> MIRPrepared:
    _validate_structure(fn)
    return MIRPrepared(fn, _analyze_presence(fn))


def _validated_function(fn: MIRFunction) -> MIRPrepared:
    prepared = _prepare_function(fn)
    if prepared.presence.issues:
        raise MIRPresenceError(prepared.presence.issues[0].message)
    return prepared


def validate_function(fn: MIRFunction) -> None:
    _validated_function(fn)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def _region_structure(fn: MIRFunction) -> MIRRegionFlow:
    regions = {r.id: r for r in fn.regions}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(regions) == len(fn.regions), "duplicate region ID")
    if regions:
        roots = [r for r in fn.regions if r.parent is None]
        _require(len(roots) == 1 and roots[0].entry == fn.entry, "invalid body region")
    for region in fn.regions:
        _require(region.id.body == fn.id and region.id.index >= 0
                 and region.entry in blocks and blocks[region.entry].region == region.id,
                 "invalid region identity or entry")
        seen = {region.id}
        parent = region.parent
        while parent is not None:
            _require(parent in regions and parent not in seen, "invalid region ancestry")
            seen.add(parent)
            parent = regions[parent].parent
    for block in fn.blocks:
        _require(block.region in regions if regions else block.region is None, "missing block region")
        _require(isinstance(block.terminator, (MIRGoto, MIRBranch, MIRReturn)), "missing or unknown terminator")
        _require(all(target is None or target in blocks for _, target in outgoing_edges(block.id, block.terminator)),
                 "invalid block target")
    for slot in fn.slots:
        local = slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
        _require(slot.residence in regions if regions and local else slot.residence is None,
                 "invalid binding residence")
        if isinstance(slot.storage_duration, MIRRegionId):
            _require(slot.storage_duration in regions and regions[slot.storage_duration].parent is not None
                     and local and slot.residence == slot.storage_duration,
                     "invalid storage region")
        elif slot.storage_duration is MIRStorageDuration.BODY and regions and local:
            _require(slot.residence == roots[0].id, "body storage has nested residence")
    flow = MIRRegionFlow(fn)
    for transition in flow.edges.values():
        for rid in transition.entered:
            _require(regions[rid].entry == transition.target, "entry into region interior")
    return flow


def operands(value: MIRRvalue) -> tuple[MIRSlotId, ...]:
    match value:
        case MIRCall(arguments=arguments):
            return arguments
        case (MIRRead(source=source) | MIRCopy(source=source)
              | MIRUnionExtract(source=source) | MIRBorrow(source=source)):
            _require(isinstance(source, MIRPlace), "invalid read place")
            return (source.root,)
        case (MIRAlias(source=source) | MIRMove(source=source)
              | MIRIteratorInit(source=source) | MIRIteratorHasNext(source=source)
              | MIRIteratorRead(source=source) | MIRIteratorAdvance(source=source)
              | MIRRangeAdvance(source=source)
              | MIRTupleCopy(source=source) | MIROptionalCopy(source=source) | MIRIsPresent(source=source)
              | MIRUnionCopy(source=source) | MIRIsAlternative(source=source)):
            return (source,)
        case MIROptionalConstruct(source=source) | MIRUnionConstruct(source=source):
            return () if source is None else (source,)
        case MIRConstruct(fields=fields):
            return fields
        case MIRTupleConstruct(elements=elements):
            return tuple(s for element in elements
                         for s in (element.fields if isinstance(element, MIRConstruct) else (element,)))
        case MIRCompare(left=left, right=right):
            return (left, right)
        case MIRNot(operand=operand):
            return (operand,)
        case MIROp(operands=values):
            return values
        case MIRConstant():
            return ()
        case _:
            raise MIRValidationError("unknown rvalue")


def successors(term: MIRGoto | MIRBranch | MIRReturn) -> tuple[MIRBlockId, ...]:
    match term:
        case MIRGoto(target=target):
            return (target,)
        case MIRBranch(then=then, otherwise=otherwise):
            return (then, otherwise)
        case MIRReturn():
            return ()
        case _:
            raise MIRValidationError("missing or unknown terminator")


def statement_reads(stmt: MIRStatement) -> tuple[MIRSlotId, ...]:
    match stmt:
        case MIRCallStmt(call=call):
            return operands(call)
        case MIRPrint(arguments=arguments):
            return arguments
        case MIRStorageInit() | MIRRecordStorageInit():
            return ()
        case MIRAssign(target=target, value=value):
            return (*operands(value), target.root) if target.projections else operands(value)
        case _:
            raise MIRValidationError("unknown instruction")


def statement_may_raise(stmt: MIRStatement, slots: 'Mapping[MIRSlotId, MIRSlot]') -> bool:
    """Whether `stmt` can exit the body by exception: a raising operation, a
    copy or constant materialization into owned storage whose allocation
    can throw (`TypeDef.copy_may_raise`), a container literal, an element
    access that checks its index or key, a call whose summary may raise, or
    a print, whose formatting allocates."""
    match stmt:
        case MIRCallStmt(call=call) | MIRAssign(value=MIRCall() as call):
            return call.may_raise
        case MIRPrint():
            return True
        case MIRAssign(target=target) if reaches_element(target):
            # An element written in place may raise: its index may be out of range.
            return True
        case MIRAssign(value=MIROp(may_raise=may_raise) | MIRCopy(may_raise=may_raise)
                       | MIRConstruct(may_raise=may_raise) | MIRRead(may_raise=may_raise)
                       | MIRBorrow(may_raise=may_raise)):
            return may_raise
        case MIRAssign(value=MIRTupleConstruct(elements=elements)):
            return any(isinstance(e, MIRConstruct) and e.may_raise for e in elements)
        case MIRAssign(target=target, value=MIRConstant()) if (
                typ := owned_leaf_place_type(target, slots)) is not None:
            return bool(type_def_of(typ).copy_may_raise)
        case MIRAssign() | MIRStorageInit() | MIRRecordStorageInit():
            return False
        case _:
            raise MIRValidationError("unknown instruction")


def owned_leaf_place_type(place: MIRPlace, slots: 'Mapping[MIRSlotId, MIRSlot]') -> TpyType | None:
    """The owned leaf `place` denotes as a whole buffer -- the body's own
    storage, what a borrowed holder points at, or an owned-leaf record
    field -- or None. Shape only; `place_info` checks the projections."""
    root = slots.get(place.root)
    if root is None:
        return None
    match place.projections:
        case ():
            return root.type if owned_storage(root) else None
        case (MIRDeref(),):
            return root.type if owned_borrow(root) else None
        case (*_, MIRField(type=typ)) if owned_leaf(typ):
            return typ
        case (*_, MIRContainerElements()):
            member = elements_member_type(place, slots)
            return member if owned_leaf(member) else None
    return None


def copies_into(source: TpyType, target: TpyType) -> bool:
    """Owned storage takes a copy of its own type, or of any member of its
    view family a view of it could hold (a `String`, or what a view views)."""
    family = view_family_of(target)
    return source == target or family is not None and view_compatible(family.view_type, source)


def body_may_raise(blocks: 'tuple[MIRBlock, ...] | list[MIRBlock]', slots: 'Mapping[MIRSlotId, MIRSlot]',
                   receiver_init: MIRReceiverInit | None) -> bool:
    """`MIRFunction.exceptional_exits`: some statement may raise, or some
    member of the receiver's entry initialization may (a composed member's
    fact is its fields', checked by the validator)."""
    return (receiver_init is not None and any(member.may_raise for member in receiver_init.fields)
            or any(statement_may_raise(stmt, slots) for block in blocks for stmt in block.statements))


def source_definition(stmt: MIRStatement) -> MIRSlotId | None:
    match stmt:
        case MIRStorageInit() | MIRRecordStorageInit() | MIRCallStmt() | MIRPrint():
            return None
        case MIRAssign(target=target):
            return None if target.projections else target.root
        case _:
            raise MIRValidationError("unknown instruction")


def _cyclic_blocks(blocks: dict[MIRBlockId, MIRBlock],
                   pred: dict[MIRBlockId, set[MIRBlockId]]) -> set[MIRBlockId]:
    """Two iterative DFS passes keep storage-site checks linear in CFG size."""
    visited: set[MIRBlockId] = set()
    order: list[MIRBlockId] = []
    for root in blocks:
        pending = [(root, False)]
        while pending:
            bid, finished = pending.pop()
            if finished:
                order.append(bid)
            elif bid not in visited:
                visited.add(bid)
                pending.append((bid, True))
                pending.extend((s, False) for s in successors(blocks[bid].terminator))
    visited.clear()
    cyclic: set[MIRBlockId] = set()
    for root in reversed(order):
        if root in visited:
            continue
        component: set[MIRBlockId] = set()
        pending_ids = [root]
        while pending_ids:
            bid = pending_ids.pop()
            if bid not in visited:
                visited.add(bid)
                component.add(bid)
                pending_ids.extend(pred[bid])
        if len(component) > 1 or root in pred[root]:
            cyclic.update(component)
    return cyclic


def _validate_structure(fn: MIRFunction) -> None:
    for summary in fn.call_summaries:
        problem = summary_problem(summary)
        _require(problem is None, problem or "invalid call summary")
    _require(len({s.callee.identity for s in fn.call_summaries}) == len(fn.call_summaries),
             "duplicate call summary identity")
    call_summaries = IdentitySet(fn.call_summaries)
    _require(fn.kind in (MIRBodyKind.FREE_FUNCTION, MIRBodyKind.METHOD, MIRBodyKind.CONSTRUCTOR),
             "unsupported body kind")
    _require((fn.kind is MIRBodyKind.CONSTRUCTOR) == (fn.receiver_init is not None),
             "constructor entry mismatch")
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(result_problem(fn.return_type, fn.borrowed_result) is None, "unsupported return type or access")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    regions = _region_structure(fn)
    records = {r.type: r for r in fn.records}
    _require(len(records) == len(fn.records), "duplicate record layout")
    _require(fn.borrowed_result is None or view_leaf(fn.borrowed_result.type)
             or native_container_type(fn.borrowed_result.type) or container_view(fn.borrowed_result.type)
             or fn.borrowed_result.type in records, "missing borrowed return record layout")
    field_types: dict[MIRFieldId, TpyType] = {}
    for record in fn.records:
        _require(type(record.opaque) is bool, "invalid record layout kind")
        if record.opaque:
            # The builtin certificate: one opaque buffer, freely copied and moved.
            _require(owned_leaf(record.type) and record.fields == ()
                     and record.copyable is True and record.movable is True, "invalid opaque leaf layout")
        else:
            _require(record_type(record.type), "unsupported record layout identity")
        _require(type(record.copyable) is bool and type(record.movable) is bool,
                 "invalid record eligibility")
        _require(isinstance(record.ancestors, tuple) and len(set(record.ancestors)) == len(record.ancestors)
                 and not (record.opaque and record.ancestors)
                 and all(isinstance(a, NominalType) and record_type(a) and a != record.type
                         for a in record.ancestors), "invalid record layout ancestors")
        owners = (record.type, *record.ancestors)
        seen: set[MIRFieldId] = set()
        for member in record.fields:
            _require(isinstance(member, MIRField) and isinstance(member.id, MIRFieldId)
                     and member.id.owner in owners and bool(member.id.name)
                     and member.id not in seen and modeled_field(member.type),
                     "invalid record layout field")
            # An owned-leaf field is a place of that leaf's opaque storage,
            # a record field a place of its member record's.
            _require(not owned_leaf(member.type) or member.type in records and records[member.type].opaque,
                     "owned-leaf field needs its opaque layout")
            bare = unwrap_readonly(member.type)
            _require(not record_type(bare) or bare in records and not records[bare].opaque,
                     "record field needs its layout")
            # A member's stored borrows would be keyed under the record around it.
            _require(not record_type(bare) or not holds_loan(bare), "record member holds a borrow")
            seen.add(member.id)
            # A field is one storage of one type in every layout carrying it
            # (its declaring owner's and each descendant's).
            _require(field_types.setdefault(member.id, member.type) == member.type,
                     "inconsistent field type")
    global_ids: set[MIRGlobalId] = set()
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        _require(isinstance(slot.record_storage, MIRRecordStorageKind), "invalid record storage kind")
        if slot.record_storage is MIRRecordStorageKind.OPTIONAL:
            _require(slot.value_kind is MIRValueKind.OWNED and not owned_leaf(slot.type)
                     and slot.kind is MIRSlotKind.LOCAL
                     and not slot.readonly and slot.storage_duration is not None,
                     "optional backing needs local record storage")
            _require(not holds_loan(slot.type), "wrapper holds a borrow")
        if slot.storage_duration is not None:
            _require(isinstance(slot.storage_duration, (MIRStorageDuration, MIRRegionId))
                     and (slot.value_kind is MIRValueKind.OWNED or scalar_wrapper(slot) or owned_tuple(slot)
                          or slot.value_kind is MIRValueKind.UNION)
                     and ((slot.storage_duration is MIRStorageDuration.CALLER
                           and slot.kind is MIRSlotKind.PARAMETER and slot.value_kind is MIRValueKind.UNION)
                          # A by-value owned leaf, an `Own[...]` container or record
                          # is the body's storage from entry to exit.
                          or (slot.storage_duration is MIRStorageDuration.BODY
                              and slot.kind is MIRSlotKind.PARAMETER and (
                                  owned_storage(slot) or owned_container(slot) or handed_record(slot)))
                          or ((slot.storage_duration is MIRStorageDuration.BODY
                               or isinstance(slot.storage_duration, MIRRegionId))
                              and slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY))),
                     "invalid storage duration fact")
        if slot.kind is MIRSlotKind.GLOBAL:
            # An owned-leaf global is external static storage, read through a readonly handle.
            _require(isinstance(slot.global_id, MIRGlobalId) and bool(slot.global_id.module and slot.global_id.name)
                     and slot.global_id not in global_ids
                     and (slot.value_kind is MIRValueKind.SCALAR or owned_borrow(slot)),
                     "invalid or duplicate global identity")
            global_ids.add(slot.global_id)
        else:
            _require(slot.global_id is None, "global identity on local slot")
        if slot.value_kind in (MIRValueKind.BORROWED_CONTAINER, MIRValueKind.NATIVE_ITERATOR):
            # A borrowed container holds a native container; an iterator
            # walks one or a view of one.
            iterator = slot.value_kind is MIRValueKind.NATIVE_ITERATOR
            _require(slot.form is Form.BORROW and isinstance(slot.container_layout, MIRContainerLayout)
                     and (native_container_type(slot.type) or iterator and container_view(slot.type)),
                     "invalid container or iterator layout")
            expected = expected_container_layout(slot.type, slot.readonly, records)
            # An iterator walks its source's declared element, one at a time.
            _require(expected is not None and slot.container_layout == expected
                     and (not iterator or binds_cursor(slot.type)), "unsupported native element")
            _require(slot.value_kind is not MIRValueKind.NATIVE_ITERATOR
                     or slot.kind is MIRSlotKind.TEMPORARY, "iterator must be an internal temporary")
        elif slot.value_kind is MIRValueKind.UNION:
            layout = slot.union_layout
            _require(isinstance(slot.type, UnionType) and isinstance(layout, MIRUnionLayout)
                     and slot.form is Form.VALUE and not slot.readonly
                     and len(layout.elements) == len(slot.type.members)
                     and len({unwrap_readonly(m) for m in slot.type.members}) == len(slot.type.members),
                     "unsupported union slot")
            kinds = set()
            for member, typ in zip(layout.elements, slot.type.members):
                if member is None:
                    _require(is_void_like_type(typ), "union absence type mismatch")
                    continue
                _require(isinstance(member, MIRTupleElement) and type(member.readonly) is bool
                         and unwrap_readonly(typ) == member.type, "union member type mismatch")
                kinds.add(member.kind)
                if member.kind is MIRValueKind.SCALAR:
                    _require(scalar_member(member), "unsupported union scalar")
                else:
                    _require(member.kind is MIRValueKind.BORROWED and record_type(member.type)
                             and (typ == member.type or member.readonly), "unsupported union reference")
                    _require(not holds_loan(member.type), "wrapper holds a borrow")
            _require(len(kinds) == 1 and len(layout.elements) >= 2, "mixed or empty union layout")
            _require(slot.storage_duration is not MIRStorageDuration.CALLER
                     or kinds == {MIRValueKind.SCALAR}, "caller duration requires borrowed scalar union wrapper")
        elif slot.value_kind is MIRValueKind.PAYLOAD_ALIAS:
            _require(storage_leaf(slot.type) and slot.form is Form.BORROW and slot.readonly
                     and slot.kind is not MIRSlotKind.PARAMETER
                     and isinstance(slot.alias_source, MIRPlace), "unsupported payload alias")
        elif slot.value_kind is MIRValueKind.OPTIONAL:
            layout = slot.optional_layout
            _require(isinstance(slot.type, OptionalType) and not slot.type.force_pointer_repr
                     and isinstance(layout, MIROptionalLayout) and slot.form is Form.VALUE
                     and not slot.readonly and type(layout.readonly) is bool, "unsupported optional slot")
            _require(unwrap_readonly(slot.type.inner) == layout.type, "optional payload type mismatch")
            if layout.kind is MIRValueKind.SCALAR:
                _require(scalar_member(layout), "unsupported optional scalar")
            else:
                _require(layout.kind is MIRValueKind.BORROWED and record_type(layout.type)
                         and (slot.type.inner == layout.type or layout.readonly), "unsupported optional reference")
                _require(not holds_loan(layout.type), "wrapper holds a borrow")
        elif slot.value_kind is MIRValueKind.TUPLE:
            layout = slot.tuple_layout
            _require(isinstance(slot.type, TupleType) and isinstance(layout, MIRTupleLayout)
                     and slot.form is (Form.STORAGE if owned_tuple(slot) else Form.VALUE)
                     and not slot.readonly, "unsupported tuple slot")
            _require(len(layout.elements) == len(slot.type.element_types), "tuple layout arity")
            if owned_tuple(slot):
                _require(slot.kind is MIRSlotKind.LOCAL and slot.storage_duration is not None,
                         "owned tuple needs local backing placement")
            for member, typ in zip(layout.elements, slot.type.element_types):
                _require(isinstance(member, MIRTupleElement) and type(member.readonly) is bool,
                         "invalid tuple element")
                if member.kind is MIRValueKind.SCALAR:
                    _require(scalar_member(member) and typ == member.type, "invalid tuple scalar")
                elif member.kind is MIRValueKind.OWNED:
                    _require(member.type in records and unwrap_readonly(typ) == member.type
                             and (typ == member.type or member.readonly), "invalid owned tuple record")
                    _require(not holds_loan(member.type), "wrapper holds a borrow")
                else:
                    _require(member.kind is MIRValueKind.BORROWED
                             and record_type(member.type) and unwrap_readonly(typ) == member.type
                             and (typ == member.type or member.readonly), "invalid tuple reference")
                    _require(not holds_loan(member.type), "wrapper holds a borrow")
        elif slot.value_kind is MIRValueKind.SCALAR:
            _require(scalar_slot(slot) and slot.form is Form.VALUE
                     and (not slot.readonly or slot.kind is MIRSlotKind.GLOBAL),
                     "unsupported slot type or form")
        elif owned_container(slot):
            # Owned container storage: mutable, placed, and of a layout its type derives.
            _require(slot.form is Form.STORAGE and not slot.readonly and slot.storage_duration is not None
                     and slot.container_layout == expected_container_layout(slot.type, False, records)
                     and (slot.kind is not MIRSlotKind.PARAMETER or slot.passing in OWNING_PASSINGS),
                     "unsupported container storage")
        elif slot.value_kind is MIRValueKind.OWNED:
            _require(slot.type in records and slot.form is Form.STORAGE
                     and records[slot.type].opaque == owned_leaf(slot.type)
                     and (slot.kind is not MIRSlotKind.PARAMETER
                          or owned_leaf(slot.type) and slot.passing in OWNING_PASSINGS
                          or handed_record(slot)),
                     "unsupported record storage type or form")
            _require(not handed_record(slot) or not holds_loan(slot.type), "owned parameter holds a borrow")
            if owned_leaf(slot.type):
                _require(not slot.readonly and slot.storage_duration is not None,
                         "owned leaf storage needs a mutable placement")
        else:
            _require(slot.value_kind is MIRValueKind.BORROWED and slot.form is Form.BORROW
                     and (record_type(slot.type) or owned_borrow(slot) and slot.type in records
                          and records[slot.type].opaque or view_holder(slot) or container_view_holder(slot)),
                     "unsupported reference slot type or form")
            # A container view's access is its element's: `Span[readonly[T]]` reads only.
            _require(not container_view_holder(slot) or slot.container_layout == expected_container_layout(
                slot.type, slot.readonly, records)
                and (slot.readonly or not readonly_elements(slot.type)),
                "invalid container view holder")
            _require(not container_view_holder(slot) or slot.kind is not MIRSlotKind.PARAMETER
                     or slot.passing is ParamPassing.VALUE, "view parameter needs a by-value passing")
            _require(not owned_leaf(slot.type) or slot.kind is not MIRSlotKind.PARAMETER
                     or slot.passing in BORROWING_PASSINGS,
                     "owned leaf parameter borrow needs a readonly passing")
            # A view parameter is the caller's loan handed over by value.
            _require(not view_leaf(slot.type) or slot.kind is not MIRSlotKind.PARAMETER
                     or slot.passing is ParamPassing.VALUE, "view parameter needs a by-value passing")
        _require(type(slot.readonly) is bool, "invalid access capability")
        _require(slot.value_kind is MIRValueKind.TUPLE or slot.tuple_layout is None,
                 "tuple layout on non-tuple slot")
        _require(slot.value_kind is MIRValueKind.OPTIONAL or slot.optional_layout is None,
                 "optional layout on non-optional slot")
        _require(slot.value_kind is MIRValueKind.UNION or slot.union_layout is None,
                 "union layout on non-union slot")
        _require(slot.value_kind is MIRValueKind.PAYLOAD_ALIAS or slot.alias_source is None,
                 "payload alias source on non-alias slot")
        _require(slot.value_kind in (MIRValueKind.BORROWED_CONTAINER, MIRValueKind.NATIVE_ITERATOR)
                 or owned_container(slot) or container_view_holder(slot)
                 or slot.container_layout is None, "container layout on unrelated slot")
        _require(isinstance(slot.kind, MIRSlotKind), "invalid slot kind")
        _require(slot.passing is None or slot.kind is MIRSlotKind.PARAMETER and isinstance(slot.passing, ParamPassing),
                 "passing fact on a non-parameter slot")

    def container_literal_ok(value: MIRConstruct, typ: TpyType, layout: MIRContainerLayout) -> bool:
        """One operand per element (a dict's keys and values alternating),
        each by its member's form: an inert leaf by value; an owned leaf
        copied through what a readonly holder lends or out of owned storage,
        or moved out of a temporary; a record copied through a holder or
        moved out of temporary record storage. Building it allocates."""
        if not isinstance(value.fields, tuple) or value.may_raise is not True:
            return False
        members = (layout.element,) if layout.value is None else (layout.element, layout.value)
        # Literal construction: the one place the compiler knows a container's length.
        if len(value.fields) % len(members) or is_array(typ) and len(value.fields) != typ.type_args[1]:
            return False
        for index, src in enumerate(value.fields):
            member, source = members[index % len(members)], slots.get(src)
            if source is None or source.kind is MIRSlotKind.GLOBAL:
                return False
            match member.kind:
                case MIRValueKind.SCALAR:
                    ok = source.value_kind is MIRValueKind.SCALAR and source.type == member.type
                case MIRValueKind.OWNED:
                    ok = (leaf_borrow(source) and copies_into(source.type, member.type)
                          or owned_storage(source) and source.type == member.type)
                case _:
                    ok = source.type == member.type and member.type in records and (
                        source.value_kind is MIRValueKind.OWNED and source.kind is MIRSlotKind.TEMPORARY
                        or source.value_kind is MIRValueKind.BORROWED and records[member.type].copyable)
            if not ok:
                return False
        return True

    def construct_members_ok(value: MIRConstruct, members: tuple[MIRField, ...]) -> bool:
        """One operand per member: an inert leaf by value, an owned leaf
        copied from what a readonly holder lends (its own borrow, a `String`
        borrow or a view of its family) or moved out of owned temporary
        storage, or a record copied through a holder of it or moved out of
        temporary record storage; the construct may raise exactly when a
        copy may."""
        if len(value.fields) != len(members) or type(value.may_raise) is not bool:
            return False
        raises = False
        for src, member in zip(value.fields, members):
            source = slots.get(src)
            if source is None:
                return False
            if record_type(bare := unwrap_readonly(member.type)):
                layout = records.get(bare)
                if layout is None or layout.opaque or source.type != bare:
                    return False
                if source.value_kind is MIRValueKind.BORROWED and layout.copyable:
                    raises = raises or record_copy_may_raise(bare)
                elif not (source.value_kind is MIRValueKind.OWNED and source.kind is MIRSlotKind.TEMPORARY
                          and not source.readonly and layout.movable):
                    return False
            elif view_leaf(member.type):
                # A view member stores the loan a readonly holder lends: its
                # own (a view) or a borrow of an owned leaf of its family.
                if not (leaf_borrow(source) and view_compatible(member.type, source.type)):
                    return False
            elif not owned_leaf(member.type):
                if source.type != member.type or source.value_kind is not MIRValueKind.SCALAR:
                    return False
            elif leaf_borrow(source) and copies_into(source.type, member.type):
                raises = raises or bool(type_def_of(member.type).copy_may_raise)
            elif not (owned_storage(source) and source.type == member.type
                      and source.kind is MIRSlotKind.TEMPORARY):
                return False
        return value.may_raise is raises

    def record_copy_may_raise(typ: TpyType) -> bool:
        return layout_copy_may_raise(records[typ], records.get)

    def record_copy_ok(value: MIRCopy, typ: TpyType) -> bool:
        """A copy of record storage of type `typ`: the owned storage a
        holder points at, the body's own record storage, or an inline record
        member; it may raise exactly when copying the layout may (or an
        element access on the way)."""
        source_type, source_kind, _ = place_info(value.source)
        projections = value.source.projections
        return (records[typ].copyable and source_type == typ and source_kind is MIRValueKind.OWNED
                and (projections == (MIRDeref(),) or not projections
                     or isinstance(projections[-1], MIRField))
                and value.may_raise is (record_copy_may_raise(typ) or reaches_element(value.source)))

    def validate_member_inits(inits: tuple[MIRMemberInit, ...], members: tuple[MIRField, ...]) -> None:
        """Entry initialization of record storage: one initializer per field
        of its layout, in layout order (the receiver's, or a composed
        member's, recursively)."""
        _require(isinstance(inits, tuple) and len(inits) == len(members), "incomplete receiver initialization")
        for init_member, member in zip(inits, members):
            validate_member_init(init_member, member)

    def validate_member_init(init_member: MIRMemberInit, member: MIRField) -> None:
        _require(isinstance(init_member, MIRMemberInit) and isinstance(init_member.mode, MIRMemberInitMode)
                 and type(init_member.may_raise) is bool, "invalid receiver initializer")
        value, mode = init_member.source, init_member.mode
        if native_container_type(member.type):
            # Copied from a container parameter, or moved from the literal
            # built over parameters; either allocates.
            layout = expected_container_layout(member.type, False, records)
            _require(layout is not None, "unsupported container field")
            match value:
                case MIRSlotId():
                    source = slots.get(value)
                    _require(mode is MIRMemberInitMode.COPY and source is not None
                             and source.kind is MIRSlotKind.PARAMETER and region_holder(source)
                             and not container_view_holder(source) and source.type == member.type,
                             "invalid receiver initializer parameter")
                case MIRConstruct():
                    _require(mode is MIRMemberInitMode.MOVE and all(
                        slots.get(o) is not None and slots[o].kind is MIRSlotKind.PARAMETER for o in value.fields)
                        and container_literal_ok(value, member.type, layout),
                        "invalid receiver initializer literal")
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            _require(init_member.may_raise is True, "receiver initializer exit fact mismatch")
            return
        if record_type(bare := unwrap_readonly(member.type)):
            # Copied through a record parameter lent readonly, moved out of
            # an `Own[R]` parameter's storage, or built by the member's own
            # constructor, field by field.
            layout = records.get(bare)
            _require(layout is not None and not layout.opaque, "record field needs its layout")
            match value:
                case MIRSlotId():
                    source = slots.get(value)
                    _require(source is not None and source.kind is MIRSlotKind.PARAMETER and source.type == bare
                             and (mode is MIRMemberInitMode.COPY and layout.copyable
                                  and source.value_kind is MIRValueKind.BORROWED and source.readonly
                                  or mode is MIRMemberInitMode.MOVE and layout.movable
                                  and source.value_kind is MIRValueKind.OWNED and not source.readonly
                                  and source.passing is ParamPassing.OWN),
                             "invalid receiver initializer parameter")
                    raises = mode is MIRMemberInitMode.COPY and record_copy_may_raise(bare)
                case MIRMemberInits():
                    _require(mode is MIRMemberInitMode.MOVE and layout.movable,
                             "invalid receiver initializer construct")
                    validate_member_inits(value.fields, layout.fields)
                    raises = any(nested.may_raise for nested in value.fields)
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            _require(init_member.may_raise is raises, "receiver initializer exit fact mismatch")
            return
        if view_leaf(member.type):
            # A view member stores the loan a lent parameter holds -- a view
            # parameter's, or a borrow of an owned leaf the caller lends --
            # or a literal's static storage; no buffer is copied.
            _require(mode is MIRMemberInitMode.BORROW and init_member.may_raise is False,
                     "invalid receiver initializer mode")
            match value:
                case MIRSlotId():
                    source = slots.get(value)
                    _require(source is not None and source.kind is MIRSlotKind.PARAMETER and leaf_borrow(source)
                             and view_compatible(member.type, source.type), "invalid receiver initializer parameter")
                case MIRConstant(value=literal):
                    _require(type(literal) in (str, bytes) and owned_constant(through_view(member.type), literal),
                             "invalid receiver initializer constant")
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            return
        if not owned_leaf(member.type):
            _require(mode is MIRMemberInitMode.SCALAR and init_member.may_raise is False,
                     "invalid receiver initializer mode")
            match value:
                case MIRSlotId():
                    _require(value in slots and slots[value].kind is MIRSlotKind.PARAMETER
                             and slots[value].value_kind is MIRValueKind.SCALAR
                             and slots[value].type == member.type, "invalid receiver initializer parameter")
                case MIRConstant(value=literal):
                    _require(leaf_constant(member.type, literal), "invalid receiver initializer constant")
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            return
        # An owned leaf is copied from a parameter's storage -- the caller's
        # through a readonly borrow (of its own type or the family's view),
        # or the body's own by-value copy -- or from a constant, or moved
        # out of the by-value copy.
        match value:
            case MIRSlotId():
                source = slots.get(value)
                _require(source is not None and source.kind is MIRSlotKind.PARAMETER and (
                    mode is MIRMemberInitMode.COPY and (
                        owned_borrow(source) and source.type == member.type
                        or owned_storage(source) and source.type == member.type)
                    or mode is MIRMemberInitMode.MOVE and owned_storage(source) and source.type == member.type),
                    "invalid receiver initializer parameter")
            case MIRConstant(value=literal):
                _require(mode is MIRMemberInitMode.COPY and owned_constant(member.type, literal),
                         "invalid receiver initializer constant")
            case _:
                raise MIRValidationError("invalid receiver initializer")
        # A move into a base's own copy that the base copies on raises like
        # the copy (`definitions._compose`).
        _require(init_member.may_raise is (mode is MIRMemberInitMode.COPY
                                           and bool(type_def_of(member.type).copy_may_raise))
                 or mode is MIRMemberInitMode.MOVE and init_member.may_raise is True
                 and bool(type_def_of(member.type).copy_may_raise),
                 "receiver initializer exit fact mismatch")

    if fn.receiver_init is not None:
        init = fn.receiver_init
        _require(isinstance(init, MIRReceiverInit) and init.receiver in slots,
                 "invalid constructor receiver")
        receiver = slots[init.receiver]
        _require(receiver.kind is MIRSlotKind.PARAMETER
                 and receiver.value_kind is MIRValueKind.BORROWED
                 and not receiver.readonly and receiver.type in records
                 and isinstance(fn.return_type, VoidType), "invalid constructor receiver")
        validate_member_inits(init.fields, records[receiver.type].fields)

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    def place_info(place: MIRPlace, *, write: bool = False) -> tuple[TpyType, MIRValueKind, bool]:
        _require(isinstance(place, MIRPlace), "invalid place")
        typ = slot_type(place.root)
        slot = slots[place.root]
        kind, readonly = slot.value_kind, slot.readonly
        _require(not (write and slot.kind is MIRSlotKind.GLOBAL and readonly), "store through readonly global")
        tuple_member = False
        optional_member = False
        # The layout of the container the place denotes so far, if it is one.
        container = slot.container_layout if region_holder(slot) else None
        # A container field written whole: no operation replaces one yet.
        whole_container = False
        # A view member: the loan stored in the record, read whole.
        view_member = False
        for projection in place.projections:
            _require(not view_member, "projection through a view member")
            if not isinstance(projection, MIRContainerElements):
                container = None
            match projection:
                case MIRContainerStructure():
                    raise MIRValidationError("summary regions are analysis places, not direct element accesses")
                case MIRContainerElements():
                    # Any element: a read, borrow or in-place write of one
                    # reaches the whole region.
                    _require(container is not None, "element projection needs container storage")
                    member = container.subscript
                    typ, readonly = member.type, readonly or member.readonly
                    kind = MIRValueKind.SCALAR if member.kind is MIRValueKind.SCALAR else MIRValueKind.OWNED
                    _require(not (write and readonly), "store through readonly container")
                    container = None
                    whole_container = False
                case MIRUnionPayload():
                    _require(kind is MIRValueKind.UNION and slot.union_layout is not None,
                             "union projection needs union payload")
                    _require(type(projection.alternative) is int
                             and 0 <= projection.alternative < len(slot.union_layout.elements), "invalid union alternative")
                    member = slot.union_layout.elements[projection.alternative]
                    _require(member is not None, "absent union alternative has no payload")
                    typ, kind, readonly = member.type, member.kind, member.readonly
                    optional_member = True
                case MIROptionalPayload():
                    _require(kind is MIRValueKind.OPTIONAL and slot.optional_layout is not None,
                             "optional projection needs optional payload")
                    member = slot.optional_layout
                    typ, kind, readonly = member.type, member.kind, member.readonly
                    optional_member = True
                case MIRTupleIndex():
                    _require(kind is MIRValueKind.TUPLE and slot.tuple_layout is not None,
                             "tuple projection needs tuple payload")
                    _require(type(projection.index) is int
                             and 0 <= projection.index < len(slot.tuple_layout.elements), "tuple index out of range")
                    member = slot.tuple_layout.elements[projection.index]
                    typ, kind, readonly = member.type, member.kind, member.readonly
                    tuple_member = True
                case MIRDeref():
                    _require(kind is MIRValueKind.BORROWED, "dereference needs reference holder")
                    _require(not (write and readonly), "store through readonly reference")
                    kind = MIRValueKind.OWNED
                    tuple_member = False
                    optional_member = False
                case MIRField():
                    _require(kind is MIRValueKind.OWNED, "field needs record storage")
                    _require(not view_leaf(typ), "field projection through a view")
                    # An owned leaf's interior is opaque: nothing projects inside its buffer.
                    _require(not (typ in records and records[typ].opaque) and not owned_leaf(typ),
                             "field projection under an opaque layout")
                    tuple_member = False
                    _require(not (write and readonly), "store through readonly storage")
                    # A field is keyed by its declaring owner: the storage's
                    # type, or an ancestor whose field its layout carries.
                    _require(isinstance(projection.id, MIRFieldId) and bool(projection.id.name) and (
                        projection.id.owner == typ
                        or typ in records and MIRField(projection.id, projection.type) in records[typ].fields),
                        "field owner mismatch")
                    member_type = unwrap_readonly(projection.type)
                    inline_record = record_type(member_type)
                    leaf_storage = owned_leaf(projection.type)
                    field_readonly = readonly or isinstance(projection.type, ReadonlyType)
                    if native_container_type(member_type):
                        # A container field is owned storage whose region a
                        # further `[elements]` reaches.
                        container = expected_container_layout(member_type, field_readonly, records)
                        _require(container is not None, "unsupported container field")
                        whole_container = leaf_storage = True
                    _require(modeled_field(projection.type), "unsupported field type")
                    if typ in records:
                        _require(projection.id in field_types, "field missing from record layout")
                    _require(field_types.setdefault(projection.id, projection.type) == projection.type,
                             "inconsistent field type")
                    typ = member_type
                    # An owned-leaf field is a place of owned storage: borrowed,
                    # copied from, and replaced in place. A view member holds
                    # a loan, as a borrowed holder does.
                    view_member = view_leaf(projection.type)
                    kind = (MIRValueKind.OWNED if inline_record or leaf_storage
                            else MIRValueKind.BORROWED if view_member else MIRValueKind.SCALAR)
                    readonly = readonly or isinstance(projection.type, ReadonlyType)
                case _:
                    raise MIRValidationError("unsupported place projections")
        _require(not (write and tuple_member), "tuple element replacement is forbidden")
        _require(not (write and optional_member), "optional payload replacement is forbidden")
        # An inline record member is replaced whole only by a member write
        # (`validate_member_write`), which every write of such a place reaches.
        _require(not (write and whole_container), "container replacement is unsupported")
        # Only the constructor's member initialization stores a loan.
        _require(not (write and view_member), "view member replacement is unsupported")
        if kind is MIRValueKind.OWNED and not place.projections and not owned_container(slot):
            _require(typ in records, "record place needs layout")
        return typ, kind, readonly

    def place_type(place: MIRPlace, *, write: bool = False) -> TpyType:
        return place_info(place, write=write)[0]

    for slot in fn.slots:
        if slot.value_kind is MIRValueKind.PAYLOAD_ALIAS:
            source = slot.alias_source
            _require(len(source.projections) == 1 and isinstance(source.projections[0], MIRUnionPayload)
                     and place_type(source) == slot.type, "invalid payload alias source")

    def compatible_element(source: MIRTupleElement | MIROptionalLayout,
                           target: MIRTupleElement | MIROptionalLayout) -> bool:
        return (source.type == target.type and source.kind is target.kind
                and (not source.readonly or target.readonly))

    def validate_call(call: MIRCall) -> None:
        _require(isinstance(call, MIRCall) and call.summary in call_summaries,
                 "call summary does not belong to this body")
        summary = call.summary
        # No declaration can say a stub cannot raise yet.
        _require(not isinstance(summary.callee, THIRStubCallee) or call.may_raise is True,
                 "stub call must be a possible exceptional exit")
        _require(type(call.may_raise) is bool and call.may_raise is (not summary.normal_return_only),
                 "call exit fact mismatch")
        _require(len(call.arguments) == len(summary.parameters), "call arity mismatch")
        for parameter, path in (*((w.parameter, w.path) for w in summary.writes),
                                *((o.parameter, o.path) for o in summary.returns)):
            # Each field of a write or return path is a member of the record
            # storage it is read from: hop 0 the record the argument binds
            # (a descendant's layout carries its ancestors' fields), hop k
            # the previous field's member record.
            argument = slots.get(call.arguments[parameter])
            storage = argument.type if argument is not None and record_type(argument.type) else None
            ref = summary.parameters[parameter].borrowed_record
            if storage is not None and ref is not None and not binds_at(records, storage, ref.type):
                continue  # the argument check below refuses the binding itself
            hops = path_hops(summary.parameters[parameter], path, storage)
            _require(hops is not None and all(
                record in records and MIRField(MIRFieldId(field.owner, field.name), field.type)
                in records[record].fields for record, field in hops),
                "call write field does not match record layout")
        # A pure stub reads every argument whatever its declared access.
        pure = isinstance(summary.callee, THIRStubCallee) and summary.callee.contract is THIRStubContract.PURE
        for sid, binding in zip(call.arguments, summary.parameters):
            _require(sid in slots, "unknown slot ID")
            source = slots[sid]
            _require(source.kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            ref, owned = binding.borrowed_record, owned_value_type(binding.type)
            if binding.protocol:
                # Only a builtin leaf's dispatch is the stub's own runtime code;
                # the leaf is read in place (a view: the leaf it views) or by
                # value. A native container is read whole by its own runtime code.
                bound = type_def_of(read_leaf(source))
                _require(region_holder(source) or bound is not None and (
                    bound.owned_leaf and leaf_borrow(source) or bound.loan_inert and scalar_slot(source)),
                         "call protocol argument is not a builtin leaf")
                continue
            if native_container_type(binding.type) or container_view(binding.type):
                # A container is lent whole (a view by value: the loan it
                # holds), or handed over as the body's own temporary.
                _require(source.type == binding.type and ref is None and (
                    binding.passing in OWNING_PASSINGS and binding.passing is not ParamPassing.VALUE
                    and owned_container(source) and source.kind is MIRSlotKind.TEMPORARY
                    or container_view(binding.type) and container_view_holder(source)
                    and binding.passing is ParamPassing.VALUE and (not source.readonly or binding.readonly)
                    or native_container_type(binding.type) and binding.passing not in OWNING_PASSINGS
                    and region_holder(source) and not container_view_holder(source)
                    and (not source.readonly or binding.readonly or pure)), "call container argument mismatch")
                continue
            if record_type(binding.type) and ref is None and binding.passing in OWNING_PASSINGS:
                # A record handed over by value (an element a container
                # takes): the temporary the call moves from.
                _require(source.type == binding.type and source.value_kind is MIRValueKind.OWNED
                         and source.kind is MIRSlotKind.TEMPORARY and binding.type in records,
                         "call record argument mismatch")
                _require(not holds_loan(binding.type), "handed-over record holds a borrow")
                continue
            if owned is not None and binding.passing in BORROWING_PASSINGS and view_holder(source):
                # A view of the parameter's family is lent as the view it already is.
                _require(view_compatible(source.type, owned), "call owned-leaf argument mismatch")
                continue
            # Record storage binds at its own type or a struct-base ancestor's;
            # every other binding is exact.
            _require(source.type == binding.type or ref is not None and ref.type == binding.type
                     and binds_at(records, source.type, binding.type), "call argument type mismatch")
            if view_leaf(binding.type):
                _require(view_holder(source) and binding.readonly, "call view argument mismatch")
            elif ref is not None:
                _require(source.value_kind is MIRValueKind.BORROWED and (not source.readonly or ref.readonly),
                         "call record argument mismatch")
            elif owned is not None:
                # Lent for the call at a borrowing passing, else the callee's own copy.
                _require(source.type == owned and (
                    owned_borrow(source) if binding.passing in BORROWING_PASSINGS
                    else owned_storage(source) and source.kind is MIRSlotKind.TEMPORARY),
                    "call owned-leaf argument mismatch")
            else:
                _require(source.type == binding.type and source.value_kind is MIRValueKind.SCALAR,
                         "call scalar argument mismatch")

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    initialized_storage: set[MIRSlotId] = set()
    owning_blocks: set[MIRBlockId] = set()

    def validate_owned_write(stmt: MIRAssign, block: MIRBlock, target: MIRSlot) -> None:
        fact, value = stmt.storage_write, stmt.value
        _require(isinstance(fact, MIRRecordWrite) and fact.rebind_owner is None
                 and fact.mode in (MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.INITIALIZE_REGION,
                                   MIRRecordWriteMode.IN_PLACE),
                 "owned leaf write needs an initialization or replacement fact")
        match fact.mode:
            case MIRRecordWriteMode.INITIALIZE_ONCE:
                _require(target.storage_duration is MIRStorageDuration.BODY, "backing write needs private body storage")
                owning_blocks.add(block.id)
            case MIRRecordWriteMode.INITIALIZE_REGION:
                # A full expression's result is initialized inside that expression's
                # own region, nested in the region that owns the destination.
                _require(isinstance(target.storage_duration, MIRRegionId)
                         and target.storage_duration in regions.chains[block.region],
                         "scoped initialization needs owning region")
                region_initializations[target.id] = block.id
        if fact.mode is not MIRRecordWriteMode.IN_PLACE:
            _require(target.id not in initialized_storage, "repeated storage initialization")
            initialized_storage.add(target.id)
        if owned_container(target):
            validate_container_value(value, target)
        else:
            validate_owned_value(value, target.type, MIRPlace(target.id))

    def validate_container_value(value: MIRRvalue, target: MIRSlot) -> None:
        """What owned container storage takes: a literal, another owned
        container moved in, a copy of a whole container place, or a call's
        owned result."""
        for operand in operands(value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        match value:
            case MIRConstruct():
                _require(container_literal_ok(value, target.type, target.container_layout),
                         "incomplete or mistyped container literal")
            case MIRMove():
                source = slots[value.source]
                _require(owned_container(source) and source.type == target.type and source.id != target.id,
                         "container move source mismatch")
            case MIRCopy():
                source_type, source_kind, _ = place_info(value.source)
                root = slots[value.source.root]
                _require(source_type == target.type and value.may_raise is True and (
                    not value.source.projections and region_holder(root) and not container_view_holder(root)
                    or bool(value.source.projections) and isinstance(value.source.projections[-1], MIRField)
                    and source_kind is MIRValueKind.OWNED),
                    "container copy source mismatch")
            case MIRCall():
                validate_call(value)
                result = value.summary.callee.signature.return_type
                _require(value.summary.borrowed_result is None
                         and unwrap_own(result) == target.type
                         and return_representation(result) is Representation.STORAGE,
                         "call container result type mismatch")
            case _:
                raise MIRValidationError("container write needs a literal, move, copy or call")

    def validate_call_record(call: MIRCall, typ: TpyType) -> None:
        """Record storage of type `typ` a call initializes or replaces: fresh
        storage the callee hands over by value (`Own[R]`), never a borrowed
        result's referent copied."""
        validate_call(call)
        _require(call.summary.callee.signature.hands_over(typ), "call record result type mismatch")
        _require(not holds_loan(typ), "call result holds a borrow")

    def validate_element_write(stmt: MIRAssign, typ: TpyType) -> None:
        """An element replaced in place (`xs[i] = v`): a weak update of the
        region, under the container's root. The value takes the member's
        form: an inert leaf read by value, an owned leaf as owned storage
        takes one, a record constructed, copied through a holder or moved
        out of a temporary."""
        fact, value = stmt.storage_write, stmt.value
        _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.IN_PLACE
                 and fact.rebind_owner == stmt.target.root, "element write needs an in-place fact")
        for operand in operands(value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        if storage_leaf(typ):
            match value:
                case MIRRead(source=source):
                    _require(not source.projections and scalar_slot(slots[source.root])
                             and slots[source.root].type == typ and value.may_raise is False,
                             "element write type mismatch")
                case MIRConstant():
                    _require(leaf_constant(typ, value.value), "constant type or range mismatch")
                case _:
                    raise MIRValidationError("element write type mismatch")
        elif owned_leaf(typ):
            validate_owned_value(value, typ, stmt.target)
        else:
            _require(typ in records and not records[typ].opaque, "element write type mismatch")
            _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove, MIRCall)), "element write type mismatch")
            validate_record_value(value, typ, stmt.target)

    def validate_record_value(value: MIRRvalue, typ: TpyType, target: MIRPlace) -> None:
        """What record storage of type `typ` takes, wherever it lives (a
        local's or a temporary's own storage, what a holder points at, an
        element, an inline member): a construct of its members, a copy of a
        record source (`record_copy_ok`), a move out of other owned record
        storage, or a call handing over a fresh result."""
        match value:
            case MIRConstruct():
                _require(construct_members_ok(value, records[typ].fields),
                         "incomplete or mistyped record construction")
            case MIRCopy():
                _require(record_copy_ok(value, typ), "record copy source or eligibility")
            case MIRMove(source=source):
                _require(source in slots and slots[source].value_kind is MIRValueKind.OWNED
                         and slots[source].type == typ and not slots[source].readonly
                         and records[typ].movable and source != target.root,
                         "record move source or eligibility")
            case MIRCall():
                validate_call_record(value, typ)
            case _:
                raise MIRValidationError("record write needs a construct, move, copy or call")

    def validate_member_write(stmt: MIRStatement, typ: TpyType) -> None:
        """An inline record member replaced whole (`ln.a = v`): in place,
        under the record a holder reaches, which keeps its identity; a
        declared `readonly[...]` member, or one reached readonly, is never
        replaced. C++ copy- or move-assigns, so the member is movable."""
        _require(isinstance(stmt, MIRAssign), "record member write needs a replacement fact")
        fact = stmt.storage_write
        _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.IN_PLACE
                 and fact.rebind_owner is None, "record member write needs a replacement fact")
        _, kind, readonly = place_info(stmt.target, write=True)
        _require(kind is MIRValueKind.OWNED and not readonly, "store through readonly storage")
        _require(typ in records and not records[typ].opaque and records[typ].movable,
                 "record member replacement needs movable record")
        _require(not holds_loan(typ), "record member holds a borrow")
        for operand in operands(stmt.value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        validate_record_value(stmt.value, typ, stmt.target)

    def validate_owned_field_write(stmt: MIRAssign, typ: TpyType) -> None:
        # A field is initialized by its constructor; any later write replaces
        # its buffer in place, under the record a borrowed holder reaches.
        fact = stmt.storage_write
        _require(isinstance(fact, MIRRecordWrite) and fact.rebind_owner is None
                 and fact.mode is MIRRecordWriteMode.IN_PLACE, "owned-leaf field write needs a replacement fact")
        validate_owned_value(stmt.value, typ, stmt.target)

    def validate_owned_value(value: MIRRvalue, typ: TpyType, target: MIRPlace) -> None:
        for operand in operands(value):
            _require(operand in slots and (slots[operand].kind is not MIRSlotKind.GLOBAL
                                           or isinstance(value, MIRCopy) and owned_borrow(slots[operand])),
                     "global value needs explicit read")
        match value:
            case MIRCopy():
                source_type, source_kind, _ = place_info(value.source)
                # The source is a whole owned buffer: the body's storage, a field, or
                # what a holder (a view holder included) points at.
                _require(copies_into(source_type, typ) and source_kind is MIRValueKind.OWNED
                         and (owned_leaf_place_type(value.source, slots) is not None
                              or value.source.projections == (MIRDeref(),)),
                         "owned leaf copy source mismatch")
                td = type_def_of(typ)
                # An element's copy also checks its index.
                _require(value.may_raise is (bool(td.copy_may_raise) or reaches_element(value.source)),
                         "owned leaf copy exit fact mismatch")
            case MIRMove():
                source = slots[value.source]
                _require(owned_storage(source) and source.type == typ and MIRPlace(value.source) != target,
                         "owned leaf move source mismatch")
            case MIROp():
                td = type_def_of(typ)
                _require(isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                         and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                         and all(scalar_slot(slots[o]) or leaf_borrow(slots[o]) for o in value.operands),
                         "primitive operation needs leaf operands and result")
                _require(td.primitive_ops and all(primitive_operand(slots[o]) for o in value.operands),
                         "primitive operation lacks the primitive contract")
                # No fact says a primitive operation cannot raise yet.
                _require(value.may_raise is True, "primitive operation must be a possible exceptional exit")
            case MIRConstant():
                _require(owned_constant(typ, value.value), "constant type or range mismatch")
            case MIRCall():
                # A callee returns an owned leaf by value: fresh storage the caller
                # owns, unless the result may borrow an argument (a holder, then a copy).
                validate_call(value)
                _require(value.summary.borrowed_result is None
                         and owned_value_type(value.summary.callee.signature.return_type) == typ,
                         "call owned result type mismatch")
            case _:
                raise MIRValidationError("owned leaf write needs a copy, move, operation, constant or call")
    payload_initializations: set[MIRSlotId] = set()
    payload_init_blocks: set[MIRBlockId] = set()
    region_initializations: dict[MIRSlotId, MIRBlockId] = {}
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, (MIRAssign, MIRStorageInit, MIRRecordStorageInit, MIRCallStmt, MIRPrint)),
                     "unknown instruction")
            target_place = statement_target(stmt)
            if isinstance(stmt, MIRPrint):
                _require(isinstance(stmt.arguments, tuple) and all(
                    sid in slots and (scalar_slot(slots[sid]) or leaf_borrow(slots[sid]))
                    and slots[sid].kind is not MIRSlotKind.GLOBAL
                    for sid in stmt.arguments), "print needs local inert leaf arguments")
                # A user method could format any other leaf (an enum's `__str__`).
                _require(all(primitive_operand(slots[sid]) for sid in stmt.arguments),
                         "print argument lacks the primitive contract")
            if isinstance(stmt, MIRCallStmt):
                validate_call(stmt.call)
                _require(isinstance(stmt.call.summary.callee.signature.return_type, VoidType),
                         "effect-only call needs void result")
            if fn.regions:
                active = regions.chains[block.region]
                targets = () if target_place is None else (target_place.root,)
                for sid in (*statement_reads(stmt), *targets):
                    _require(sid in slots, "unknown slot ID")
                    residence = slots[sid].residence
                    _require(residence is None or residence in active, "use outside binding residence")
            if target_place is None:
                continue
            target_type = place_type(target_place, write=True)
            target = slots[target_place.root]
            if isinstance(stmt, (MIRStorageInit, MIRRecordStorageInit)):
                match stmt:
                    case MIRRecordStorageInit():
                        _require(not stmt.target.projections
                                 and target.record_storage is MIRRecordStorageKind.OPTIONAL,
                                 "empty initialization needs optional record backing")
                    case MIRStorageInit():
                        _require(not stmt.target.projections and target.kind is MIRSlotKind.LOCAL
                                 and scalar_wrapper(target), "physical initialization needs local scalar wrapper")
                        _require(type(stmt.alternative) is int and stmt.alternative == 0
                                 and isinstance(stmt.value, MIRConstant), "invalid wrapper default")
                        default_type = (None if target.value_kind is MIRValueKind.OPTIONAL
                                        or target.union_layout.elements[0] is None else target.union_layout.elements[0].type)
                        expected = None if default_type is None else zero_value_of(default_type)
                        _require((default_type is None or expected is not None)
                                 and type(stmt.value.value) is type(expected) and stmt.value.value == expected,
                                 "invalid wrapper default value")
                _require(target.id not in payload_initializations, "repeated payload initialization")
                payload_initializations.add(target.id)
                if isinstance(target.storage_duration, MIRRegionId):
                    _require(target.storage_duration == block.region, "scoped initialization needs owning region")
                    region_initializations[target.id] = block.id
                else:
                    _require(target.storage_duration is MIRStorageDuration.BODY
                             and (not fn.regions or block.region == blocks[fn.entry].region),
                             "physical initialization needs owning placement")
                    payload_init_blocks.add(block.id)
                continue
            value = stmt.value
            fact = stmt.storage_write
            if not stmt.target.projections and (owned_storage(target) or owned_container(target)):
                # Every write to owned storage carries its storage event, whatever produces the value.
                validate_owned_write(stmt, block, target)
                continue
            if elements_place(stmt.target):
                validate_element_write(stmt, target_type)
                continue
            if stmt.target.projections and owned_leaf_place_type(stmt.target, slots) is not None:
                validate_owned_field_write(stmt, target_type)
                continue
            if (stmt.target.projections and isinstance(member := stmt.target.projections[-1], MIRField)
                    and record_type(unwrap_readonly(member.type))):
                validate_member_write(stmt, target_type)
                continue
            if isinstance(fact, MIRTupleInitialization):
                _require(not stmt.target.projections and owned_tuple(target)
                         and isinstance(value, MIRTupleConstruct), "tuple initialization needs owning tuple construction")
                _require(target.id not in initialized_storage, "repeated tuple initialization")
                initialized_storage.add(target.id)
                if isinstance(target.storage_duration, MIRRegionId):
                    _require(target.storage_duration == block.region, "scoped initialization needs owning region")
                    region_initializations[target.id] = block.id
                else:
                    _require(target.storage_duration is MIRStorageDuration.BODY
                             and (not fn.regions or block.region == blocks[fn.entry].region),
                             "tuple initialization needs owning placement")
                    owning_blocks.add(block.id)
            elif isinstance(fact, MIRPayloadWrite):
                _require(isinstance(fact.mode, MIRPayloadWriteMode), "invalid payload write mode")
                _require(not stmt.target.projections and target.kind is MIRSlotKind.LOCAL
                         and scalar_wrapper(target)
                         and isinstance(value, (MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy)),
                         "payload write needs local scalar wrapper operation")
                if fact.mode in (MIRPayloadWriteMode.INITIALIZE, MIRPayloadWriteMode.INITIALIZE_REGION):
                    _require(target.id not in payload_initializations, "repeated payload initialization")
                    payload_initializations.add(target.id)
                    if fact.mode is MIRPayloadWriteMode.INITIALIZE_REGION:
                        _require(isinstance(target.storage_duration, MIRRegionId)
                                 and target.storage_duration == block.region, "scoped initialization needs owning region")
                        region_initializations[target.id] = block.id
                    else:
                        _require(not isinstance(target.storage_duration, MIRRegionId),
                                 "scoped storage needs activation initialization")
                        payload_init_blocks.add(block.id)
            elif isinstance(fact, MIRRecordWrite):
                _require(isinstance(fact.mode, MIRRecordWriteMode), "invalid record write fact")
                _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove, MIRCall)),
                         "record write on non-record operation")
                match fact.mode:
                    case MIRRecordWriteMode.OPTIONAL_ASSIGN:
                        _require(not stmt.target.projections
                                 and target.record_storage is MIRRecordStorageKind.OPTIONAL
                                 and fact.rebind_owner is None,
                                 "invalid optional backing assignment")
                    case MIRRecordWriteMode.INITIALIZE_REGION:
                        # Initialized in the owning region, or inside the full
                        # expression nested in it that builds its arguments.
                        _require(not stmt.target.projections and target.value_kind is MIRValueKind.OWNED
                                 and isinstance(target.storage_duration, MIRRegionId)
                                 and target.storage_duration in regions.chains[block.region]
                                 and fact.rebind_owner is None,
                                 "scoped initialization needs owning region")
                        region_initializations[target.id] = block.id
                    case MIRRecordWriteMode.INITIALIZE_ONCE | MIRRecordWriteMode.OWN_SITE:
                        _require(not stmt.target.projections and target.value_kind is MIRValueKind.OWNED
                                 and target.storage_duration is MIRStorageDuration.BODY and fact.rebind_owner is None,
                                 "backing write needs private body storage")
                    case MIRRecordWriteMode.IN_PLACE:
                        _require(fact.rebind_owner == stmt.target.root
                                 and ((target.value_kind is MIRValueKind.BORROWED
                                       and stmt.target.projections == (MIRDeref(),))
                                      or (target.value_kind is MIRValueKind.OPTIONAL
                                          and stmt.target.projections == (MIROptionalPayload(), MIRDeref()))),
                                 "invalid in-place rebind owner or target")
                        _require(not holds_loan(target_type), "in-place replacement holds a borrow")
            else:
                _require(fact is None, "invalid storage write fact")
            if owned_tuple(target) and not stmt.target.projections:
                _require(isinstance(fact, MIRTupleInitialization), "owned tuple needs initialization fact")
            if target.record_storage is MIRRecordStorageKind.OPTIONAL and not stmt.target.projections:
                _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN,
                         "optional backing needs explicit assignment fact")
            if not stmt.target.projections:
                _require(target.value_kind is not MIRValueKind.PAYLOAD_ALIAS or isinstance(value, MIRUnionExtract),
                         "payload alias requires extraction")
            for operand in operands(value):
                slot_type(operand)
                _require(slots[operand].kind is not MIRSlotKind.GLOBAL or isinstance(value, MIRRead)
                         or isinstance(value, (MIRBorrow, MIRCopy)) and owned_borrow(slots[operand]),
                         "global value needs explicit read")
            match value:
                case MIRRangeAdvance():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.id == source.id
                             and target.type == INT32 and target.value_kind is MIRValueKind.SCALAR
                             and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.step) is int and value.step in (-1, 1), "invalid range induction")
                case MIRIteratorInit():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.NATIVE_ITERATOR
                             and region_holder(source) and target.type == source.type
                             and target.container_layout == with_access(source.container_layout, target.readonly)
                             and (not source.readonly or target.readonly), "iterator source or access mismatch")
                case MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    source = slots[value.source]
                    _require(source.value_kind is MIRValueKind.NATIVE_ITERATOR
                             and not stmt.target.projections, "iterator operation needs iterator source and local target")
                    member = source.container_layout.element
                    match value:
                        case MIRIteratorHasNext():
                            _require(target_type == BOOL and target.value_kind is MIRValueKind.SCALAR,
                                     "iterator test needs bool target")
                        case MIRIteratorAdvance():
                            _require(target.id == source.id, "advance must update its iterator")
                        case MIRIteratorRead() if member.kind is MIRValueKind.OWNED:
                            # An owned-leaf element is read through a borrow of it or a view.
                            _require(target.kind is not MIRSlotKind.PARAMETER and leaf_borrow(target)
                                     and (target.type == member.type or view_compatible(target.type, member.type)),
                                     "iterator element type or access mismatch")
                        case MIRIteratorRead():
                            _require(target.type == member.type and target.value_kind is member.kind
                                     and target.kind is not MIRSlotKind.PARAMETER
                                     and (member.kind is MIRValueKind.SCALAR
                                          or not (member.readonly or source.readonly) or target.readonly),
                                     "iterator element type or access mismatch")
                case MIRUnionConstruct() | MIRUnionCopy():
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.UNION
                             and target.kind is not MIRSlotKind.PARAMETER, "union operation needs local destination")
                    if isinstance(value, MIRUnionCopy):
                        source = slots[value.source]
                        _require(source.value_kind is MIRValueKind.UNION and source.type == target.type,
                                 "union copy layout mismatch")
                        _require(all(a is b if a is None or b is None else compatible_element(a, b)
                                     for a, b in zip(source.union_layout.elements, target.union_layout.elements)),
                                 "union copy increases access")
                    else:
                        _require(type(value.alternative) is int
                                 and 0 <= value.alternative < len(target.union_layout.elements), "invalid union construction alternative")
                        member = target.union_layout.elements[value.alternative]
                        _require((member is None) == (value.source is None), "union construction payload mismatch")
                        if member is not None:
                            source = slots[value.source]
                            _require(compatible_element(MIRTupleElement(source.type, source.value_kind, source.readonly), member),
                                     "union construction type or access mismatch")
                case MIRIsAlternative():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.SCALAR
                             and target_type == BOOL and source.value_kind is MIRValueKind.UNION,
                             "union test type mismatch")
                    _require(bool(value.alternatives) and len(set(value.alternatives)) == len(value.alternatives)
                             and all(type(i) is int and 0 <= i < len(source.union_layout.elements)
                                     for i in value.alternatives), "invalid tested union alternatives")
                case MIRUnionExtract():
                    source = value.source
                    _require(not stmt.target.projections and len(source.projections) == 1
                             and isinstance(source.projections[0], MIRUnionPayload)
                             and target.kind is not MIRSlotKind.PARAMETER, "unsupported union extraction")
                    _require(place_type(source) == target_type, "union extraction type mismatch")
                    member = slots[source.root].union_layout.elements[source.projections[0].alternative]
                    if member.kind is MIRValueKind.SCALAR:
                        _require(target.value_kind is MIRValueKind.PAYLOAD_ALIAS and target.alias_source == source,
                                 "scalar extraction must bind payload storage")
                    else:
                        _require(target.value_kind is MIRValueKind.BORROWED
                                 and (not member.readonly or target.readonly), "union extraction increases access")
                case MIROptionalConstruct() | MIROptionalCopy():
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.OPTIONAL
                             and target.kind is not MIRSlotKind.PARAMETER, "optional operation needs local destination")
                    if isinstance(value, MIROptionalCopy):
                        source = slots[value.source]
                        _require(source.value_kind is MIRValueKind.OPTIONAL, "optional copy needs optional source")
                        member = source.optional_layout
                    elif value.source is not None:
                        source = slots[value.source]
                        member = MIROptionalLayout(source.type, source.value_kind, source.readonly)
                    else:
                        member = target.optional_layout
                    _require(compatible_element(member, target.optional_layout), "optional payload type or access mismatch")
                case MIRIsPresent():
                    _require(target_type == BOOL and slots[value.source].value_kind is MIRValueKind.OPTIONAL,
                             "presence test needs optional source and bool destination")
                case MIRTupleConstruct() | MIRTupleCopy():
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.TUPLE
                             and target.kind is not MIRSlotKind.PARAMETER,
                             "tuple operation needs tuple destination")
                    if isinstance(value, MIRTupleConstruct):
                        _require(len(value.elements) == len(target.tuple_layout.elements),
                                 "tuple payload type or access mismatch")
                        elements = []
                        for source, member in zip(value.elements, target.tuple_layout.elements):
                            if isinstance(source, MIRConstruct):
                                _require(member.kind is MIRValueKind.OWNED,
                                         "tuple constructor needs inline record member")
                                _require(construct_members_ok(source, records[member.type].fields),
                                         "incomplete or mistyped tuple record construction")
                                elements.append(member)
                            else:
                                _require(member.kind is not MIRValueKind.OWNED,
                                         "inline tuple member needs constructor")
                                elements.append(MIRTupleElement(slots[source].type, slots[source].value_kind,
                                                               slots[source].readonly))
                    else:
                        source = slots[value.source]
                        _require(source.value_kind is MIRValueKind.TUPLE, "tuple copy needs tuple source")
                        _require(not owned_tuple(source) and not owned_tuple(target), "owning tuple copy is unsupported")
                        elements = source.tuple_layout.elements
                    _require(len(elements) == len(target.tuple_layout.elements)
                             and all(compatible_element(src, dst)
                                     for src, dst in zip(elements, target.tuple_layout.elements)),
                             "tuple payload type or access mismatch")
                case MIRConstruct() | MIRCopy() | MIRMove() | MIRCall() if (
                        not isinstance(value, MIRCall) or isinstance(fact, MIRRecordWrite)):
                    if fact is None or fact.mode not in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.INITIALIZE_REGION,
                                                         MIRRecordWriteMode.OPTIONAL_ASSIGN, MIRRecordWriteMode.IN_PLACE):
                        owning_blocks.add(block.id)
                    target = slots[stmt.target.root]
                    _require(target_type in records and (
                        (not stmt.target.projections and target.value_kind is MIRValueKind.OWNED)
                        or stmt.target.projections in ((MIRDeref(),), (MIROptionalPayload(), MIRDeref()))),
                        "record destination type")
                    if fact is not None and fact.mode in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.OPTIONAL_ASSIGN):
                        _require(records[target_type].movable, "reusable backing needs movable record")
                    if not stmt.target.projections:
                        _require(target.record_storage is MIRRecordStorageKind.OPTIONAL
                                 or stmt.target.root not in initialized_storage, "repeated storage initialization")
                        initialized_storage.add(stmt.target.root)
                    else:
                        _require(records[target_type].movable
                                 and (isinstance(value, MIRConstruct)
                                      or fact is not None and fact.mode is MIRRecordWriteMode.IN_PLACE),
                                 "unsupported record replacement")
                    validate_record_value(value, target_type, stmt.target)
                case MIRBorrow():
                    target = slots[stmt.target.root]
                    source_type, source_kind, readonly = place_info(value.source)
                    element = elements_place(value.source)
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER,
                             "storage borrow type mismatch")
                    if container_view_holder(target):
                        # A view of the whole region (an unstepped slice): it
                        # never raises, and it views the region's member type.
                        _require(element and not value.may_raise
                                 and target.container_layout.element.type == source_type,
                                 "container view borrow type mismatch")
                    elif target.value_kind is MIRValueKind.BORROWED_CONTAINER:
                        # A holder of a whole container place (a field).
                        _require(not element and source_kind is MIRValueKind.OWNED and source_type == target_type
                                 and not value.may_raise, "container borrow type mismatch")
                    elif source_kind is MIRValueKind.BORROWED:
                        # A view member read: the holder takes the loan stored there.
                        _require(view_holder(target) and bool(value.source.projections)
                                 and isinstance(value.source.projections[-1], MIRField)
                                 and view_leaf(source_type) and view_compatible(target_type, source_type),
                                 "storage borrow type mismatch")
                    else:
                        _require(target.value_kind is MIRValueKind.BORROWED
                                 and source_kind is MIRValueKind.OWNED
                                 and (target_type == source_type
                                      or view_holder(target) and view_compatible(target_type, source_type)),
                                 "storage borrow type mismatch")
                    _require(not readonly or target.readonly, "borrow increases access")
                case MIRCall():
                    validate_call(value)
                    # A follows-receiver result has the access of the receiver bound at this call.
                    result = (value.summary.result_at(slots[value.arguments[0]].readonly) if value.arguments
                              else value.summary.borrowed_result)
                    _require(not stmt.target.projections, "call needs whole result holder")
                    if result is None:
                        # An `Own[T]` scalar result is a plain T.
                        _require(scalar_slot(target)
                                 and target_type == unwrap_own(value.summary.callee.signature.return_type),
                                 "call result type or target mismatch")
                    else:
                        _require((target.value_kind is MIRValueKind.BORROWED
                                  or target.value_kind is MIRValueKind.BORROWED_CONTAINER
                                  and native_container_type(result.type))
                                 and target_type == result.type and (not result.readonly or target.readonly),
                                 "call result type or access mismatch")
                case MIRConstant() if leaf_borrow(target):
                    # A str or bytes literal lives in static storage its holder borrows.
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.value) in (str, bytes) and owned_constant(read_leaf(target), value.value),
                             "static literal needs a borrowed holder of its type")
                case MIRConstant():
                    _require(leaf_constant(target_type, value.value), "constant type or range mismatch")
                case MIRRead():
                    _require(target.value_kind is not MIRValueKind.PAYLOAD_ALIAS and storage_leaf(target_type)
                             and target_type == place_type(value.source), "read type mismatch")
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                case MIRAlias():
                    target = slots[stmt.target.root]
                    source = slots[value.source]
                    _require(not stmt.target.projections
                             and target.value_kind in (MIRValueKind.BORROWED, MIRValueKind.BORROWED_CONTAINER)
                             and source.value_kind is target.value_kind
                             and (source.container_layout is None) == (target.container_layout is None)
                             and (target.container_layout is None
                                  or target.container_layout == with_access(source.container_layout, target.readonly))
                             and (target_type == source.type
                                  or view_holder(target) and leaf_borrow(source)
                                  and view_compatible(target_type, source.type)), "alias type mismatch")
                    _require(target.kind is not MIRSlotKind.PARAMETER, "reference parameter reseat")
                    _require(not source.readonly or target.readonly, "alias increases access")
                case MIRCompare():
                    _require(value.op in COMPARISON_OPS,
                             "unsupported comparison")
                    _require(target_type == BOOL, "comparison result is not bool")
                    left, right = slots[value.left], slots[value.right]
                    certified = certified_primitive_comparison(left.type, right.type)
                    _require(all(scalar_slot(s) or leaf_borrow(s) for s in (left, right))
                             and (left.type == right.type or certified),
                             "comparison operand type mismatch")
                    _require(certified and primitive_operand(left) and primitive_operand(right),
                             "comparison lacks the primitive contract")
                case MIRNot():
                    _require(target_type == BOOL and slot_type(value.operand) == BOOL
                             and slots[value.operand].value_kind is MIRValueKind.SCALAR,
                             "not operand or result is not bool")
                case MIROp():
                    _require(not stmt.target.projections and scalar_slot(target)
                             and target.kind is not MIRSlotKind.GLOBAL
                             and isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                             and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                             and all(scalar_slot(slots[o]) or leaf_borrow(slots[o]) for o in value.operands),
                             "primitive operation needs inert leaf operands and result")
                    _require(primitive_leaf(target_type) and all(primitive_operand(slots[o]) for o in value.operands),
                             "primitive operation lacks the primitive contract")
                    _require(value.may_raise is True, "primitive operation must be a possible exceptional exit")
                case _:
                    raise MIRValidationError("unknown rvalue semantics")
        term = block.terminator
        if fn.regions:
            used = (term.condition,) if isinstance(term, MIRBranch) else (
                (term.value,) if isinstance(term, MIRReturn) and term.value is not None else ())
            for sid in used:
                _require(sid in slots, "unknown slot ID")
                _require(slots[sid].residence is None or slots[sid].residence in regions.chains[block.region],
                         "use outside binding residence")
        for successor in successors(term):
            _require(successor in blocks, "invalid block target")
            pred[successor].add(block.id)
        match term:
            case MIRBranch():
                _require(slot_type(term.condition) == BOOL and slots[term.condition].value_kind is MIRValueKind.SCALAR,
                         "branch condition is not bool")
                _require(slots[term.condition].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            case MIRReturn():
                if term.value is None:
                    _require(isinstance(fn.return_type, VoidType), "missing return value")
                elif fn.borrowed_result is not None:
                    source = slots[term.value]
                    _require((source.value_kind is MIRValueKind.BORROWED
                              or source.value_kind is MIRValueKind.BORROWED_CONTAINER
                              and native_container_type(fn.borrowed_result.type))
                             and source.type == fn.borrowed_result.type
                             and (not source.readonly or fn.borrowed_result.readonly),
                             "borrowed return type or access mismatch")
                elif owned_storage(slots[term.value]):
                    # An owned leaf returns by value: its storage is copied or moved out.
                    _require(slot_type(term.value) == owned_value_type(fn.return_type), "return type mismatch")
                elif owned_container(slots[term.value]):
                    # The body's own container is moved out as the result.
                    _require(slot_type(term.value) == unwrap_own(fn.return_type), "return type mismatch")
                elif owned_record_result(fn.return_type) is not None:
                    # The body's own record storage (a local's, or an `Own[R]`
                    # parameter's) is moved out: a readonly or non-movable
                    # record would be copied instead.
                    source = slots[term.value]
                    _require(source.value_kind is MIRValueKind.OWNED
                             and (source.kind is not MIRSlotKind.PARAMETER or handed_record(source))
                             and source.type == owned_record_result(fn.return_type) and not source.readonly
                             and source.type in records and records[source.type].movable,
                             "owned record return needs movable owned storage")
                    _require(not holds_loan(source.type), "owned result holds a borrow")
                else:
                    _require(slot_type(term.value) == fn.return_type and slots[term.value].value_kind is MIRValueKind.SCALAR,
                             "return type mismatch")
                    _require(slots[term.value].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")

    exceptional = body_may_raise(fn.blocks, slots, fn.receiver_init)
    _require(type(fn.exceptional_exits) is bool and fn.exceptional_exits is exceptional,
             "exceptional exit fact mismatch")
    cyclic = _cyclic_blocks(blocks, pred)
    _require(not owning_blocks.intersection(cyclic), "owning operation in cycle")
    _require(not payload_init_blocks.intersection(cyclic), "payload initialization in cycle")
    for sid, initialization in region_initializations.items():
        owner = slots[sid].storage_duration
        pending = list(successors(blocks[initialization].terminator))
        seen: set[MIRBlockId] = set()
        while pending:
            bid = pending.pop()
            if owner not in regions.chains[blocks[bid].region] or bid in seen:
                continue
            if bid == initialization:
                loc = next((stmt.loc for stmt in blocks[initialization].statements
                            if (target := statement_target(stmt)) is not None and target.root == sid), None)
                raise MIRRepeatedInitializationError(loc)
            seen.add(bid)
            pending.extend(successors(blocks[bid].terminator))

    reachable: set[MIRBlockId] = set()
    pending = [fn.entry]
    while pending:
        bid = pending.pop()
        if bid not in reachable:
            reachable.add(bid)
            pending.extend(successors(blocks[bid].terminator))
    parameters = {s.id for s in fn.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
    writes = {bid: {sid for s in blocks[bid].statements if (sid := source_definition(s)) is not None}
              for bid in reachable}
    physical_writes = {bid: {target.root for s in blocks[bid].statements
                             if (target := statement_target(s)) is not None and not target.projections
                             and (slots[target.root].record_storage is not MIRRecordStorageKind.OPTIONAL
                                  or isinstance(s, MIRRecordStorageInit))}
                       for bid in reachable}
    # Intersection is a must analysis; initialize at top, with a synthetic
    # parameter-only incoming edge at entry even if the entry has a back edge.
    incoming = {bid: set(slots) for bid in reachable}
    outgoing = {bid: set(slots) for bid in reachable}
    physical_in = {bid: set(slots) for bid in reachable}
    physical_out = {bid: set(slots) for bid in reachable}
    work = deque(reachable)
    queued = set(reachable)
    incoming_edges = {bid: [] for bid in reachable}
    for edge, transition in regions.edges.items():
        if edge.source in reachable and transition.target is not None:
            incoming_edges[transition.target].append((edge.source, transition.reset, transition.ended))
    while work:
        bid = work.popleft()
        queued.remove(bid)
        sources = [outgoing[source] - reset for source, reset, _ in incoming_edges[bid]]
        constructed = [physical_out[source] - ended for source, _, ended in incoming_edges[bid]]
        if bid == fn.entry:
            sources.append(parameters)
            constructed.append(parameters)
        new_in = set.intersection(*sources)
        new_out = new_in | writes[bid]
        new_physical_in = set.intersection(*constructed)
        new_physical_out = new_physical_in | physical_writes[bid]
        incoming[bid] = new_in
        physical_in[bid] = new_physical_in
        if new_out != outgoing[bid] or new_physical_out != physical_out[bid]:
            outgoing[bid] = new_out
            physical_out[bid] = new_physical_out
            for target in successors(blocks[bid].terminator):
                if target not in queued:
                    work.append(target)
                    queued.add(target)
    for bid in reachable:
        assigned = incoming[bid].copy()
        constructed = physical_in[bid].copy()
        block = blocks[bid]
        for stmt in block.statements:
            reads = set(statement_reads(stmt))
            if not reads <= assigned:
                raise MIRDefiniteAssignmentError("read before definite assignment")
            _require(all(sid in constructed for sid in reads if scalar_wrapper(slots[sid])),
                     "read before storage initialization")
            if isinstance(stmt, MIRAssign) and stmt.storage_write == MIRPayloadWrite(MIRPayloadWriteMode.ASSIGN):
                _require(stmt.target.root in constructed, "assignment before storage initialization")
            if (isinstance(stmt, MIRAssign) and not stmt.target.projections
                    and stmt.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE)
                    and owned_storage(slots[stmt.target.root])):
                _require(stmt.target.root in constructed, "replacement before storage initialization")
            if (isinstance(stmt, MIRAssign) and not stmt.target.projections
                    and slots[stmt.target.root].record_storage is MIRRecordStorageKind.OPTIONAL):
                _require(stmt.target.root in constructed, "record assignment before wrapper initialization")
            if (definition := source_definition(stmt)) is not None:
                assigned.add(definition)
            target = statement_target(stmt)
            if (target is not None and not target.projections
                    and (slots[target.root].record_storage is not MIRRecordStorageKind.OPTIONAL
                         or isinstance(stmt, MIRRecordStorageInit))):
                constructed.add(target.root)
        term = block.terminator
        match term:
            case MIRBranch():
                if term.condition not in assigned:
                    raise MIRDefiniteAssignmentError("branch before definite assignment")
            case MIRReturn() if term.value is not None:
                if term.value not in assigned:
                    raise MIRDefiniteAssignmentError("return before definite assignment")
