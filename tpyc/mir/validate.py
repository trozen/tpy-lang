"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..identity_map import IdentityMap, IdentitySet
from ..parse import SourceLocation
from ..thir.nodes import COMPARISON_OPS, THIRBorrowedRecord, THIRStubCallee, THIRStubContract
from ..type_def_registry import is_array
from ..thir.scalar_leaves import (
    MIRClass, leaf_constant, modeled_field, owned_constant, owned_value_type,
    primitive_leaf, record_type, value_class,
)
from ..type_def_registry import type_def_of, zero_value_of
from ..typesys import (
    BOOL, INT32, NominalType, TpyType, VoidType,
    certified_primitive_comparison, through_view, unwrap_own,
    unwrap_readonly, view_family_of,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRGoto,
    MIRFunction, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRRvalue, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRReceiverInit, MIRBodyKind, MIRGlobalId, MIRMemberInit,
    MIRMemberInitMode, MIRMemberInits, MIRRecordWrite, MIRRecordWriteMode, MIRPayloadWrite, MIRPayloadWriteMode,
    MIRStorageInit, MIRRecordStorageInit, MIRStatement, MIRRegionId, MIREdge, MIRRecordStorageKind,
    MIRTupleInitialization, MIRTupleConstruct, MIRTupleCopy, MIRIsPresent, MIROptionalConstruct, MIROptionalCopy,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext,
    MIRIteratorRead, MIRIteratorAdvance, MIRRangeAdvance, MIROp, MIRPrint, statement_target, place_shape, view_member,
)
from .value import (
    AGGREGATE_CLASSES, CONTAINER_CLASSES, MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRShape,
    MIRValidationError,
    admitted_members, member_compatible, require_valid as _require, shape_problem, thir_member,
    with_access,
)
from .presence import MIRPresence, _analyze_presence
from .coverage import primitive_operand, read_leaf, slot_representation, view_compatible
from .region_flow import MIRRegionFlow, outgoing_edges
from .call_contract import (
    MIRParameterBinding, binds_at, crossed_records, owned_aggregate_result, signature_result, parameter_contract_problem,
    result_problem, summary_problem, MIRCallSummary, MIRPathWalks,
)
from .definitions import MIRAggregateFacts, aggregate_facts, owned_aggregate_parameter
from .shapes import (
    REGION_END, inline_payload_holder, walk, MIRContainerElements, MIRDeref, MIROptionalPayload, MIRTupleIndex,
    MIRUnionPayload, object_prefix, store_problem,
)


def handed_storage(slot: MIRSlot, bindings: 'Mapping[MIRSlotId, MIRParameterBinding]') -> bool:
    """An `Own[...]` parameter's aggregate storage (`owned_aggregate_parameter`
    of its binding, whose value the slot holds), the body's own from entry
    to exit (the caller materialized it)."""
    binding = bindings.get(slot.id)
    return (binding is not None and owned_aggregate_parameter(binding) is not None
            and slot.storage_duration is MIRStorageDuration.BODY)


def _empty_optionals(fn: MIRFunction) -> frozenset[MIRSlotId]:
    """The Optional temporaries every write leaves empty: what a call's
    `absent` parameter may name."""
    written: dict[MIRSlotId, bool] = {}
    for block in fn.blocks:
        for stmt in block.statements:
            target = statement_target(stmt)
            if target is not None:
                empty = (isinstance(stmt, MIRAssign) and not target.projections
                         and stmt.value == MIROptionalConstruct())
                written[target.root] = written.get(target.root, True) and empty
    return frozenset(s.id for s in fn.slots
                     if s.kind is MIRSlotKind.TEMPORARY and s.shape.cls is MIRClass.OPTIONAL
                     and written.get(s.id, False))


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
              | MIRUnionExtract(source=source) | MIRBorrow(source=source)
              | MIROptionalCopy(source=source) | MIRIsPresent(source=source)
              | MIRUnionCopy(source=source) | MIRIsAlternative(source=source)):
            _require(isinstance(source, MIRPlace), "invalid read place")
            return (source.root,)
        case (MIRAlias(source=source) | MIRMove(source=source)
              | MIRIteratorInit(source=source) | MIRIteratorHasNext(source=source)
              | MIRIteratorRead(source=source) | MIRIteratorAdvance(source=source)
              | MIRRangeAdvance(source=source) | MIRTupleCopy(source=source)):
            return (source,)
        case MIROptionalConstruct(source=source) | MIRUnionConstruct(source=source):
            _require(source is None or isinstance(source, MIRPlace), "invalid read place")
            return () if source is None else (source.root,)
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


def statement_may_raise(stmt: MIRStatement, slots: 'Mapping[MIRSlotId, MIRSlot]',
                        records: 'Mapping[TpyType, MIRRecordLayout]') -> bool:
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
                       | MIRBorrow(may_raise=may_raise) | MIROptionalCopy(may_raise=may_raise)):
            return may_raise
        case MIRAssign(value=MIRTupleConstruct(elements=elements)):
            return any(isinstance(e, MIRConstruct) and e.may_raise for e in elements)
        case MIRAssign(target=target, value=MIRConstant()) if (
                typ := owned_leaf_place_type(target, slots, records)) is not None:
            return bool(type_def_of(typ).copy_may_raise)
        case MIRAssign() | MIRStorageInit() | MIRRecordStorageInit():
            return False
        case _:
            raise MIRValidationError("unknown instruction")


def owned_leaf_place_type(place: MIRPlace, slots: 'Mapping[MIRSlotId, MIRSlot]',
                          records: 'Mapping[TpyType, MIRRecordLayout]') -> TpyType | None:
    """The owned leaf `place` denotes as a whole buffer -- the body's own
    storage, what a borrowed holder points at, an owned-leaf record field
    or element -- or None."""
    if place.root not in slots:
        return None
    shape = place_shape(place, slots, records)
    return shape.type if shape.leaf_storage else None


def copies_into(source: TpyType, target: TpyType) -> bool:
    """Owned storage takes a copy of its own type, or of any member of its
    view family a view of it could hold (a `String`, or what a view views)."""
    family = view_family_of(target)
    return source == target or family is not None and view_compatible(family.view_type, source)


def body_may_raise(blocks: 'tuple[MIRBlock, ...] | list[MIRBlock]', slots: 'Mapping[MIRSlotId, MIRSlot]',
                   records: 'Mapping[TpyType, MIRRecordLayout]', receiver_init: MIRReceiverInit | None) -> bool:
    """`MIRFunction.exceptional_exits`: some statement may raise, or some
    member of the receiver's entry initialization may (a composed member's
    fact is its fields', checked by the validator)."""
    return (receiver_init is not None and any(member.may_raise for member in receiver_init.fields)
            or any(statement_may_raise(stmt, slots, records) for block in blocks for stmt in block.statements))


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
    # Each summary's path walks, made once by its contract check and read by every call of it.
    # Over the body's layouts: whether a path's records verify is the summary's and the
    # lowering's check (the validator holds no definitions).
    summary_walks: IdentityMap[MIRCallSummary, MIRPathWalks] = IdentityMap()
    for summary in fn.call_summaries:
        summary_walks[summary] = walks = {}
        problem = summary_problem(summary, fn.record_layouts, walks)
        _require(problem is None, problem or "invalid call summary")
    _require(len({s.callee.identity for s in fn.call_summaries}) == len(fn.call_summaries),
             "duplicate call summary identity")
    call_summaries = IdentitySet(fn.call_summaries)
    _require(fn.kind in (MIRBodyKind.FREE_FUNCTION, MIRBodyKind.METHOD, MIRBodyKind.CONSTRUCTOR),
             "unsupported body kind")
    _require((fn.kind is MIRBodyKind.CONSTRUCTOR) == (fn.receiver_init is not None),
             "constructor entry mismatch")
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(result_problem(fn.return_type, fn.borrowed_result, fn.return_representation) is None,
             "unsupported return type or access")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    _require(all(isinstance(b, MIRParameterBinding) for b in fn.parameter_bindings), "invalid parameter binding")
    # The pairing reads each slot's kind; a count mismatch refuses there.
    _require(all(isinstance(s.kind, MIRSlotKind) for s in fn.slots), "invalid slot kind")
    bindings = fn.slot_bindings
    regions = _region_structure(fn)
    records = fn.record_layouts
    _require(len(records) == len(fn.records), "duplicate record layout")

    def defined(typ: TpyType) -> bool:
        """Whether the body supplies the definition storage of `typ` has: a
        record's or an owned leaf's layout, a container's registered
        definition (`MIRFunction.containers`)."""
        cls = value_class(typ)
        if cls is MIRClass.CONTAINER:
            return typ in records.containers
        return typ in records and records[typ].opaque == (cls is MIRClass.OWNED_LEAF)

    def facts(typ: TpyType) -> MIRAggregateFacts:
        """The copy and move facts of record or container storage of `typ`
        over the body's layouts (`aggregate_facts`)."""
        found = aggregate_facts(typ, records.get)
        _require(found is not None, "record place needs layout")
        return found
    # A record result's fields need its layout; a container's members come from its type.
    _require(fn.borrowed_result is None
             or value_class(fn.borrowed_result.type) in CONTAINER_CLASSES | {MIRClass.VIEW}
             or fn.borrowed_result.type in records, "missing borrowed return record layout")
    field_types: dict[MIRFieldId, TpyType] = {}

    def shape_at(place: MIRPlace) -> MIRShape:
        return place_shape(place, slots, records)

    for record in fn.records:
        _require(type(record.opaque) is bool, "invalid record layout kind")
        if record.opaque:
            # The builtin certificate: one opaque buffer, freely copied and moved.
            _require(value_class(record.type) is MIRClass.OWNED_LEAF and record.fields == ()
                     and record.copyable is True and record.movable is True, "invalid opaque leaf layout")
        else:
            _require(value_class(record.type) is MIRClass.RECORD, "unsupported record layout identity")
        _require(type(record.copyable) is bool and type(record.movable) is bool,
                 "invalid record eligibility")
        _require(isinstance(record.ancestors, tuple) and len(set(record.ancestors)) == len(record.ancestors)
                 and not (record.opaque and record.ancestors)
                 and all(isinstance(a, NominalType) and value_class(a) is MIRClass.RECORD and a != record.type
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
            cls = value_class(member.type)
            bare = unwrap_readonly(member.type)
            _require(cls is not MIRClass.OWNED_LEAF or bare in records and records[bare].opaque,
                     "owned-leaf field needs its opaque layout")
            _require(cls is not MIRClass.RECORD or bare in records and not records[bare].opaque,
                     "record field needs its layout")
            # An Optional record field stores its payload record inline.
            _require(cls is not MIRClass.OPTIONAL or bare.inner in records and not records[bare.inner].opaque,
                     "optional record field needs its payload layout")
            seen.add(member.id)
            # A field is one storage of one type in every layout carrying it
            # (its declaring owner's and each descendant's).
            _require(field_types.setdefault(member.id, member.type) == member.type,
                     "inconsistent field type")
    global_ids: set[MIRGlobalId] = set()
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        _require(isinstance(slot.record_storage, MIRRecordStorageKind), "invalid record storage kind")
        shape = slot.shape
        _require(isinstance(shape, MIRShape) and isinstance(shape.cls, MIRClass)
                 and isinstance(shape.holding, MIRHolding) and type(shape.readonly) is bool
                 and isinstance(shape.members, tuple)
                 and all(m is None or isinstance(m, MIRShape) and type(m.readonly) is bool for m in shape.members),
                 "invalid slot shape")
        # Every rule below reads the class: it is the type's, checked once
        # here with the rest of the shape's intrinsic rules.
        problem = shape_problem(shape, slot.type, representation=slot_representation(slot, bindings))
        _require(problem is None, f"slot {problem}")
        if slot.record_storage is MIRRecordStorageKind.OPTIONAL:
            _require(shape.is_storage and shape.cls is not MIRClass.OWNED_LEAF
                     and slot.kind is MIRSlotKind.LOCAL
                     and not slot.readonly and slot.storage_duration is not None,
                     "optional backing needs local record storage")
        if slot.storage_duration is not None:
            _require(isinstance(slot.storage_duration, (MIRStorageDuration, MIRRegionId))
                     and (shape.is_storage or slot.shape.scalar_wrapper or slot.shape.owned_tuple
                          or shape.cls is MIRClass.UNION)
                     and ((slot.storage_duration is MIRStorageDuration.CALLER
                           and slot.kind is MIRSlotKind.PARAMETER and shape.cls is MIRClass.UNION)
                          # A by-value owned leaf, an `Own[...]` container or record
                          # is the body's storage from entry to exit.
                          or (slot.storage_duration is MIRStorageDuration.BODY
                              and slot.kind is MIRSlotKind.PARAMETER and slot.shape.is_storage)
                          or ((slot.storage_duration is MIRStorageDuration.BODY
                               or isinstance(slot.storage_duration, MIRRegionId))
                              and slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY))),
                     "invalid storage duration fact")
        if slot.kind is MIRSlotKind.GLOBAL:
            # An owned-leaf global is external static storage, read through a readonly handle.
            _require(isinstance(slot.global_id, MIRGlobalId) and bool(slot.global_id.module and slot.global_id.name)
                     and slot.global_id not in global_ids
                     and (shape.is_scalar or slot.shape.owned_leaf_borrow),
                     "invalid or duplicate global identity")
            global_ids.add(slot.global_id)
        else:
            _require(slot.global_id is None, "global identity on local slot")
        # A container's members are its type's (`shape_problem`); the
        # layouts this body supplies for them are checked here.
        _require(shape.cls not in CONTAINER_CLASSES
                 or not isinstance(admitted_members(slot.type, slot.readonly, records), str),
                 "unsupported native element")
        if shape.holding is MIRHolding.CURSOR:
            _require(slot.kind is MIRSlotKind.TEMPORARY, "iterator must be an internal temporary")
        elif shape.cls is MIRClass.UNION:
            _require(slot.storage_duration is not MIRStorageDuration.CALLER or shape.scalar_wrapper,
                     "caller duration requires borrowed scalar union wrapper")
        elif shape.holding is MIRHolding.ALIAS:
            _require(isinstance(slot.alias_source, MIRPlace), "unsupported payload alias")
        elif shape.cls is MIRClass.TUPLE:
            if slot.shape.owned_tuple:
                _require(slot.kind is MIRSlotKind.LOCAL and slot.storage_duration is not None,
                         "owned tuple needs local backing placement")
            _require(all(not m.is_storage or m.type in records for m in shape.elements),
                     "invalid owned tuple record")
        elif shape.is_scalar:
            _require(not slot.readonly or slot.kind is MIRSlotKind.GLOBAL,
                     "unsupported slot type or form")
        elif shape.is_storage:
            # Owned storage is placed (mutable: `shape_problem`), of a type
            # whose definition the body supplies.
            _require(defined(slot.type) and (slot.kind is not MIRSlotKind.PARAMETER or slot.storage_duration is not None),
                     "unsupported record storage type or form")
            _require(slot.storage_duration is not None,
                     "owned storage needs a placement")
        elif shape.borrow_holder:
            _require(not slot.shape.owned_leaf_borrow or slot.type in records and records[slot.type].opaque,
                     "unsupported reference slot type or form")
        _require(shape.holding is MIRHolding.ALIAS or slot.alias_source is None,
                 "payload alias source on non-alias slot")
    # Each parameter slot holds its binding's value.
    for sid, binding in bindings.items():
        bound = replace(binding, value=slots[sid].shape)
        problem = parameter_contract_problem(bound)
        _require(problem is None, f"parameter {problem}")
        _require(bound == binding, "parameter slot differs from its binding")

    def container_literal_ok(value: MIRConstruct, typ: TpyType, members: tuple[MIRShape, ...], *,
                             entry: bool = False) -> bool:
        """One operand per element (a dict's keys and values alternating),
        each by its member's form: an inert leaf by value; an owned leaf
        copied through what a readonly holder lends or out of owned storage,
        or moved out of a temporary; a record copied through a holder or
        moved out of temporary record storage. At entry (`entry`, a
        receiver member's literal) a leaf element may be a constant, as a
        composed record member's field may. Building it allocates."""
        if not isinstance(value.fields, tuple) or value.may_raise is not True:
            return False
        # Literal construction: the one place the compiler knows a container's length.
        if len(value.fields) % len(members) or is_array(typ) and len(value.fields) != typ.type_args[1]:
            return False
        for index, src in enumerate(value.fields):
            member = members[index % len(members)]
            if isinstance(src, MIRConstant):
                if not entry or not (member.cls is MIRClass.LEAF and leaf_constant(member.type, src.value)
                                     or member.cls is MIRClass.OWNED_LEAF and owned_constant(member.type, src.value)):
                    return False
                continue
            source = slots.get(src)
            if source is None or source.kind is MIRSlotKind.GLOBAL:
                return False
            match member.cls:
                case MIRClass.LEAF:
                    ok = source.shape.is_scalar and source.type == member.type
                case MIRClass.OWNED_LEAF:
                    ok = (source.shape.leaf_borrow and copies_into(source.type, member.type)
                          or source.shape.leaf_storage and source.type == member.type)
                case _:
                    ok = source.type == member.type and member.type in records and (
                        source.shape.is_storage and source.kind is MIRSlotKind.TEMPORARY
                        or source.shape.is_pointer and facts(member.type).copyable)
            if not ok:
                return False
        return True

    def construct_members_ok(value: MIRConstruct, members: tuple[MIRField, ...]) -> bool:
        """One operand per member: an inert leaf by value, an owned leaf
        copied from what a readonly holder lends (its own borrow, a `String`
        borrow or a view of its family) or moved out of owned temporary
        storage, or a record or container copied through a holder of it or
        moved out of temporary storage of its type; the construct may raise
        exactly when a copy may."""
        if len(value.fields) != len(members) or type(value.may_raise) is not bool:
            return False
        raises = False
        for src, member in zip(value.fields, members):
            source = slots.get(src)
            if source is None:
                return False
            cls, bare = value_class(member.type), unwrap_readonly(member.type)
            if cls is MIRClass.OPTIONAL:
                # The payload a borrowed Optional holder lends, if engaged, is
                # put into the member's inline payload storage (the definition
                # decided copy or move; an empty one copies nothing).
                payload = bare.inner
                if not (source.shape.record_payload and source.shape.payload.type == payload
                        and records.get(payload) is not None):
                    return False
                raises = raises or record_copy_may_raise(payload)
            elif cls in AGGREGATE_CLASSES:
                found = aggregate_facts(bare, records.get) if defined(bare) else None
                if found is None or source.type != bare:
                    return False
                if source.shape.is_pointer and found.copyable:
                    raises = raises or found.copy_may_raise
                elif not (source.shape.is_storage and source.kind is MIRSlotKind.TEMPORARY
                          and not source.readonly and found.movable):
                    return False
            elif cls is MIRClass.VIEW:
                # A view member stores the loan a readonly holder lends: its
                # own (a view) or a borrow of an owned leaf of its family.
                if not (source.shape.leaf_borrow and view_compatible(member.type, source.type)):
                    return False
            elif cls is not MIRClass.OWNED_LEAF:
                if source.type != member.type or not source.shape.is_scalar:
                    return False
            elif source.shape.leaf_borrow and copies_into(source.type, member.type):
                raises = raises or bool(type_def_of(member.type).copy_may_raise)
            elif not (source.shape.leaf_storage and source.type == member.type
                      and source.kind is MIRSlotKind.TEMPORARY):
                return False
        return value.may_raise is raises

    def record_copy_may_raise(typ: TpyType) -> bool:
        return facts(typ).copy_may_raise

    def record_copy_ok(value: MIRCopy, typ: TpyType) -> bool:
        """A copy of record or container storage of type `typ`: the owned
        storage a holder points at, the body's own storage, or an inline
        member; it may raise exactly when copying it may (or an element
        access on the way)."""
        source = place_info(value.source)
        projections = value.source.projections
        return (facts(typ).copyable and source.type == typ and source.owned_place
                and (projections in ((), object_prefix(slots[value.source.root].shape, payload=False))
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
        cls, bare = value_class(member.type), unwrap_readonly(member.type)
        if cls in AGGREGATE_CLASSES:
            # Copied through a parameter lent readonly, moved out of an
            # `Own[...]` parameter's storage, or built over the parameters --
            # a record by the member's own constructor, field by field, a
            # container by its literal, which allocates.
            found = aggregate_facts(bare, records.get) if defined(bare) else None
            _require(found is not None, "record field needs its layout")
            match value:
                case MIRSlotId():
                    source = slots.get(value)
                    _require(source is not None and source.kind is MIRSlotKind.PARAMETER and source.type == bare
                             and (mode is MIRMemberInitMode.COPY and found.copyable
                                  and source.shape.is_pointer and source.readonly
                                  or mode is MIRMemberInitMode.MOVE and found.movable
                                  and handed_storage(source, fn.slot_bindings)),
                             "invalid receiver initializer parameter")
                    raises = mode is MIRMemberInitMode.COPY and found.copy_may_raise
                case MIRConstruct() if cls is MIRClass.CONTAINER:
                    members = admitted_members(bare, False, records)
                    _require(not isinstance(members, str), "unsupported container field")
                    _require(mode is MIRMemberInitMode.MOVE and all(
                        isinstance(o, MIRConstant) or slots.get(o) is not None
                        and slots[o].kind is MIRSlotKind.PARAMETER for o in value.fields)
                        and container_literal_ok(value, bare, members, entry=True),
                        "invalid receiver initializer literal")
                    raises = True
                case MIRMemberInits() if cls is MIRClass.RECORD:
                    _require(mode is MIRMemberInitMode.MOVE and found.movable,
                             "invalid receiver initializer construct")
                    validate_member_inits(value.fields, records[bare].fields)
                    raises = any(nested.may_raise for nested in value.fields)
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            _require(init_member.may_raise is raises, "receiver initializer exit fact mismatch")
            return
        if cls is MIRClass.OPTIONAL:
            payload = bare.inner
            # Empty, copied from the payload an Optional record parameter
            # lends, or engaged with a payload its own constructor builds.
            layout = records.get(payload)
            _require(layout is not None and not layout.opaque, "optional record field needs its payload layout")
            match value:
                case MIROptionalConstruct(source=None):
                    _require(mode is MIRMemberInitMode.SCALAR, "invalid receiver initializer mode")
                    raises = False
                case MIRSlotId():
                    source = slots.get(value)
                    _require(source is not None and source.kind is MIRSlotKind.PARAMETER
                             and mode is MIRMemberInitMode.COPY and layout.copyable
                             and source.shape.record_payload and source.shape.payload.type == payload,
                             "invalid receiver initializer parameter")
                    raises = record_copy_may_raise(payload)
                case MIRMemberInits():
                    _require(mode is MIRMemberInitMode.MOVE and layout.movable, "invalid receiver initializer construct")
                    validate_member_inits(value.fields, layout.fields)
                    raises = any(nested.may_raise for nested in value.fields)
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            _require(init_member.may_raise is raises, "receiver initializer exit fact mismatch")
            return
        if cls is MIRClass.VIEW:
            # A view member stores the loan a lent parameter holds -- a view
            # parameter's, or a borrow of an owned leaf the caller lends --
            # or a literal's static storage; no buffer is copied.
            _require(mode is MIRMemberInitMode.BORROW and init_member.may_raise is False,
                     "invalid receiver initializer mode")
            match value:
                case MIRSlotId():
                    source = slots.get(value)
                    _require(source is not None and source.kind is MIRSlotKind.PARAMETER and source.shape.leaf_borrow
                             and view_compatible(member.type, source.type), "invalid receiver initializer parameter")
                case MIRConstant(value=literal):
                    _require(type(literal) in (str, bytes) and owned_constant(through_view(member.type), literal),
                             "invalid receiver initializer constant")
                case _:
                    raise MIRValidationError("invalid receiver initializer")
            return
        if cls is not MIRClass.OWNED_LEAF:
            _require(mode is MIRMemberInitMode.SCALAR and init_member.may_raise is False,
                     "invalid receiver initializer mode")
            match value:
                case MIRSlotId():
                    _require(value in slots and slots[value].kind is MIRSlotKind.PARAMETER
                             and slots[value].shape.is_scalar
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
                        source.shape.owned_leaf_borrow and source.type == member.type
                        or source.shape.leaf_storage and source.type == member.type)
                    or mode is MIRMemberInitMode.MOVE and source.shape.leaf_storage and source.type == member.type),
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
        # The built receiver is the body's receiver, parameter 0: never
        # another parameter of the record's type.
        _require(isinstance(init, MIRReceiverInit) and bool(bindings)
                 and init.receiver == next(iter(bindings)),
                 "invalid constructor receiver")
        receiver = slots[init.receiver]
        _require(receiver.shape.is_pointer
                 and not receiver.readonly and receiver.type in records
                 and isinstance(fn.return_type, VoidType), "invalid constructor receiver")
        validate_member_inits(init.fields, records[receiver.type].fields)

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    def place_info(place: MIRPlace, *, write: bool = False) -> MIRShape:
        _require(isinstance(place, MIRPlace), "invalid place")
        slot_type(place.root)
        slot = slots[place.root]
        _require(not (write and slot.kind is MIRSlotKind.GLOBAL and slot.readonly), "store through readonly global")
        shape = slot.shape
        tuple_member = False
        optional_member = False
        walked = walk(shape, place.projections, records)
        for hop in walked.hops:
            projection, shape = hop.projection, hop.reached
            _require(shape is not None, REGION_END)
            match projection:
                case MIROptionalPayload():
                    _require(not shape.is_storage or shape.type in records, "optional payload needs its layout")
                    optional_member = True
                case MIRUnionPayload():
                    optional_member = True
                case MIRTupleIndex():
                    tuple_member = True
                case MIRDeref():
                    tuple_member = False
                    optional_member = False
                case MIRField():
                    tuple_member = False
                    _require(shape.cls is not MIRClass.CONTAINER or bool(shape.members), "unsupported container field")
                    _require(field_types.setdefault(projection.id, projection.type) == projection.type,
                             "inconsistent field type")
        problem = store_problem(walked) if write else None
        _require(problem is None, problem)
        _require(not (write and tuple_member), "tuple element replacement is forbidden")
        _require(not (write and optional_member), "optional payload replacement is forbidden")
        # An aggregate is replaced whole only in place through its holder
        # (`IN_PLACE`) or as a member (`validate_member_write`); a view
        # member is rebound only by a view member write
        # (`validate_view_member_write`): every write of such a place reaches one.
        if shape.is_storage and not place.projections:
            _require(defined(shape.type), "record place needs layout")
        return shape

    def place_type(place: MIRPlace, *, write: bool = False) -> TpyType:
        return place_info(place, write=write).type

    def payload_operand(place: MIRPlace) -> MIRShape:
        """The payload a wrapper construction stores: a holder's whole value."""
        _require(isinstance(place, MIRPlace) and not place.projections, "wrapper payload operand must be a holder")
        slot_type(place.root)
        return slots[place.root].shape

    for slot in fn.slots:
        if slot.shape.holding is MIRHolding.ALIAS:
            source = slot.alias_source
            _require(len(source.projections) == 1 and isinstance(source.projections[0], MIRUnionPayload)
                     and place_type(source) == slot.type, "invalid payload alias source")

    empty_optionals = _empty_optionals(fn)

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
        _require(isinstance(call.absent, frozenset) and all(
            type(i) is int and 0 <= i < len(call.arguments) and call.arguments[i] in empty_optionals
            for i in call.absent), "call absent argument is not an empty Optional")
        def storage_of(parameter: int) -> NominalType | None:
            argument = slots.get(call.arguments[parameter])
            return argument.type if argument is not None and record_type(argument.type) else None

        def unbound(parameter: int, storage: NominalType | None) -> bool:
            # The argument check below refuses the binding itself.
            ref = summary.parameters[parameter].lent_record
            return storage is not None and ref is not None and not binds_at(records, storage, ref.type)
        # Each record a published path reads fields of has its layout here.
        _require(all(record in records for record in crossed_records(summary, summary_walks[summary], storage_of,
                                                                     unbound)), "call path record has no layout")
        # A pure stub reads every argument whatever its declared access.
        pure = isinstance(summary.callee, THIRStubCallee) and summary.callee.contract is THIRStubContract.PURE
        for sid, binding in zip(call.arguments, summary.parameters):
            _require(sid in slots, "unknown slot ID")
            source = slots[sid]
            _require(source.kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            if binding.protocol:
                # Only a builtin leaf's dispatch is the stub's own runtime code;
                # the leaf is read in place (a view: the leaf it views) or by
                # value. A native container is read whole by its own runtime code.
                bound = type_def_of(read_leaf(source))
                _require(source.shape.container_or_view or bound is not None and (
                    bound.owned_leaf and source.shape.leaf_borrow or bound.loan_inert and source.shape.is_scalar),
                         "call protocol argument is not a builtin leaf")
                continue
            cls = binding.cls
            if cls is MIRClass.CONTAINER_VIEW:
                # A view by value: the loan it holds.
                _require(source.type == binding.type and source.shape.container_view_holder
                         and (not source.readonly or binding.readonly), "call container argument mismatch")
                continue
            if cls in AGGREGATE_CLASSES and binding.storage:
                # An aggregate handed over by value (an `Own[...]` parameter,
                # an element a container takes): the temporary the call moves from.
                _require(source.type == binding.type and source.shape.is_storage
                         and source.kind is MIRSlotKind.TEMPORARY and defined(binding.type),
                         "call record argument mismatch")
                continue
            if cls is MIRClass.OWNED_LEAF and not binding.storage and source.shape.view_holder:
                # A view of the parameter's family is lent as the view it already is.
                _require(view_compatible(source.type, binding.type), "call owned-leaf argument mismatch")
                continue
            if cls is MIRClass.OPTIONAL:
                # An Optional holder of the payload the binding borrows, or of its scalar.
                _require(source.type == binding.type and source.shape.cls is MIRClass.OPTIONAL
                         and member_compatible(source.shape.payload, binding.value.payload),
                         "call optional argument mismatch")
                continue
            # Record storage binds at its own type or a struct-base ancestor's;
            # every other binding is exact.
            _require(source.type == binding.type or cls is MIRClass.RECORD
                     and binds_at(records, source.type, binding.type), "call argument type mismatch")
            if cls is MIRClass.VIEW:
                _require(source.shape.view_holder and binding.readonly, "call view argument mismatch")
            elif cls in AGGREGATE_CLASSES:
                # A record or container lent through a holder of it; a pure
                # stub reads its receiver whatever access it declares.
                _require(source.shape.borrowed_aggregate and source.shape.cls is cls
                         and (not source.readonly or binding.readonly or pure), "call record argument mismatch")
            elif cls is MIRClass.OWNED_LEAF:
                # Lent for the call at a borrowing passing, else the callee's own copy.
                _require(source.type == binding.type and (
                    source.shape.leaf_storage and source.kind is MIRSlotKind.TEMPORARY if binding.storage
                    else source.shape.owned_leaf_borrow), "call owned-leaf argument mismatch")
            else:
                _require(source.type == binding.type and source.shape.is_scalar,
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
        validate_owned_value(value, target.type, MIRPlace(target.id))

    def validate_call_record(call: MIRCall, typ: TpyType) -> None:
        """Record storage of type `typ` a call initializes or replaces: fresh
        storage the callee hands over by value (`Own[R]`), never a borrowed
        result's referent copied. The loans it stores are the summary's
        transfers into the result."""
        validate_call(call)
        _require(call.summary.callee.signature.hands_over(typ), "call record result type mismatch")

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
        cls = place_info(stmt.target).cls
        if cls is MIRClass.LEAF:
            match value:
                case MIRRead(source=source):
                    _require(not source.projections and slots[source.root].shape.is_scalar
                             and slots[source.root].type == typ and value.may_raise is False,
                             "element write type mismatch")
                case MIRConstant():
                    _require(leaf_constant(typ, value.value), "constant type or range mismatch")
                case _:
                    raise MIRValidationError("element write type mismatch")
        elif cls is MIRClass.OWNED_LEAF:
            validate_owned_value(value, typ, stmt.target)
        else:
            _require(value_class(typ) in AGGREGATE_CLASSES and defined(typ), "element write type mismatch")
            _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove, MIRCall)), "element write type mismatch")
            validate_record_value(value, typ, stmt.target)

    def validate_record_value(value: MIRRvalue, typ: TpyType, target: MIRPlace) -> None:
        """What record or container storage of type `typ` takes, wherever
        it lives (a local's or a temporary's own storage, what a holder
        points at, an element, an inline member): a construct of its
        members (a container's literal of the members the target has), a
        copy of an aggregate source (`record_copy_ok`), a move out of other
        owned storage of its type, or a call handing over a fresh result."""
        for operand in operands(value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        match value:
            case MIRConstruct() if value_class(typ) is MIRClass.CONTAINER:
                _require(container_literal_ok(value, typ, place_info(target).members),
                         "incomplete or mistyped container literal")
            case MIRConstruct():
                _require(construct_members_ok(value, records[typ].fields),
                         "incomplete or mistyped record construction")
            case MIRCopy():
                _require(record_copy_ok(value, typ), "record copy source or eligibility")
            case MIRMove(source=source):
                _require(source in slots and slots[source].shape.is_storage
                         and slots[source].type == typ and not slots[source].readonly
                         and facts(typ).movable and source != target.root,
                         "record move source or eligibility")
            case MIRCall():
                validate_call_record(value, typ)
            case _:
                raise MIRValidationError("record write needs a construct, move, copy or call")

    def validate_member_write(stmt: MIRStatement, typ: TpyType) -> None:
        """An inline record or container member replaced whole (`ln.a = v`,
        `self.items = data`): in place, under the record a holder reaches,
        which keeps its identity; a declared `readonly[...]` member, or one
        reached readonly, is never replaced. C++ copy- or move-assigns, so
        the member is movable."""
        _require(isinstance(stmt, MIRAssign), "record member write needs a replacement fact")
        fact = stmt.storage_write
        _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.IN_PLACE
                 and fact.rebind_owner is None, "record member write needs a replacement fact")
        target = place_info(stmt.target, write=True)
        _require(target.owned_place and not target.readonly, "store through readonly storage")
        _require(defined(typ) and facts(typ).movable, "record member replacement needs movable record")
        for operand in operands(stmt.value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        validate_record_value(stmt.value, typ, stmt.target)

    def validate_optional_field_write(stmt: MIRStatement) -> None:
        """`h.p = v` on an Optional record field: the whole field replaced
        in place under the record a holder reaches, which keeps its
        identity. The value empties it, moves a record built in temporary
        storage into its payload, or copies another Optional's payload."""
        _require(isinstance(stmt, MIRAssign), "optional field write needs a replacement fact")
        fact = stmt.storage_write
        _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.IN_PLACE
                 and fact.rebind_owner is None, "optional field write needs a replacement fact")
        target = place_info(stmt.target, write=True)
        _require(target.cls is MIRClass.OPTIONAL and not target.readonly, "store through readonly storage")
        payload = target.payload.type
        layout = records.get(payload)
        _require(layout is not None and not layout.opaque, "optional record field needs its payload layout")
        for operand in operands(stmt.value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        match stmt.value:
            case MIROptionalConstruct(source=None):
                pass
            case MIROptionalConstruct(source=source):
                operand = payload_operand(source)
                _require(operand.type == payload and operand.is_storage and not operand.readonly
                         and slots[source.root].kind is MIRSlotKind.TEMPORARY and layout.movable,
                         "optional field payload needs a moved temporary")
            case MIROptionalCopy(source=source):
                wrapper = place_info(source)
                _require(wrapper.cls is MIRClass.OPTIONAL and wrapper.payload.type == payload
                         and wrapper.payload.cls is MIRClass.RECORD
                         and layout.copyable, "optional field copy source mismatch")
                _require(stmt.value.may_raise is (record_copy_may_raise(payload) or reaches_element(source)),
                         "optional field copy exit fact mismatch")
            case _:
                raise MIRValidationError("optional field write needs an optional construction or copy")

    def validate_view_member_write(stmt: MIRAssign, typ: TpyType) -> None:
        """`t.s = v`: a view member rebound to the loan a view holder holds,
        under a record a mutable path reaches. It replaces no storage, so it
        carries no storage event."""
        _require(stmt.storage_write is None, "view member write needs no storage fact")
        target = place_info(stmt.target, write=True)
        _require(target.is_pointer and not target.readonly, "store through readonly storage")
        source = slots.get(stmt.value.source) if isinstance(stmt.value, MIRAlias) else None
        _require(source is not None and source.kind is not MIRSlotKind.GLOBAL and source.shape.view_holder
                 and view_compatible(typ, source.type), "view member write needs a view holder")

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
                                           or isinstance(value, MIRCopy) and slots[operand].shape.owned_leaf_borrow),
                     "global value needs explicit read")
        match value:
            case MIRCopy():
                source = place_info(value.source)
                # The source is a whole owned buffer: the body's storage, a field, or
                # what a holder (a view holder included) points at.
                _require(copies_into(source.type, typ) and source.owned_place
                         and (owned_leaf_place_type(value.source, slots, records) is not None
                              or value.source.projections
                              == object_prefix(slots[value.source.root].shape, payload=False)),
                         "owned leaf copy source mismatch")
                td = type_def_of(typ)
                # An element's copy also checks its index.
                _require(value.may_raise is (bool(td.copy_may_raise) or reaches_element(value.source)),
                         "owned leaf copy exit fact mismatch")
            case MIRMove():
                source = slots[value.source]
                _require(source.shape.leaf_storage and source.type == typ and MIRPlace(value.source) != target,
                         "owned leaf move source mismatch")
            case MIROp():
                td = type_def_of(typ)
                _require(isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                         and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                         and all(slots[o].shape.is_scalar or slots[o].shape.leaf_borrow for o in value.operands),
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
                         and owned_value_type(signature_result(value.summary.callee.signature)) == typ,
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
                    sid in slots and (slots[sid].shape.is_scalar or slots[sid].shape.leaf_borrow)
                    and slots[sid].kind is not MIRSlotKind.GLOBAL
                    for sid in stmt.arguments), "print needs local inert leaf arguments")
                # A user method could format any other leaf (an enum's `__str__`).
                _require(all(primitive_operand(slots[sid]) for sid in stmt.arguments),
                         "print argument lacks the primitive contract")
            if isinstance(stmt, MIRCallStmt):
                validate_call(stmt.call)
                _require(isinstance(signature_result(stmt.call.summary.callee.signature), VoidType),
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
            target_shape = place_info(target_place, write=True)
            target_type = target_shape.type
            target = slots[target_place.root]
            if isinstance(stmt, (MIRStorageInit, MIRRecordStorageInit)):
                match stmt:
                    case MIRRecordStorageInit():
                        _require(not stmt.target.projections
                                 and target.record_storage is MIRRecordStorageKind.OPTIONAL,
                                 "empty initialization needs optional record backing")
                    case MIRStorageInit():
                        _require(not stmt.target.projections and target.kind is MIRSlotKind.LOCAL
                                 and target.shape.scalar_wrapper, "physical initialization needs local scalar wrapper")
                        _require(type(stmt.alternative) is int and stmt.alternative == 0
                                 and isinstance(stmt.value, MIRConstant), "invalid wrapper default")
                        layout = shape_at(stmt.target)
                        default_type = (None if layout.cls is not MIRClass.UNION
                                        or layout.alternatives[0] is None else layout.alternatives[0].type)
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
            if not stmt.target.projections and target.shape.leaf_storage:
                # Every write to owned storage carries its storage event, whatever produces the value.
                validate_owned_write(stmt, block, target)
                continue
            if view_member(stmt.target) is not None:
                validate_view_member_write(stmt, target_type)
                continue
            if elements_place(stmt.target):
                validate_element_write(stmt, target_type)
                continue
            if stmt.target.projections and owned_leaf_place_type(stmt.target, slots, records) is not None:
                validate_owned_field_write(stmt, target_type)
                continue
            if (stmt.target.projections and isinstance(member := stmt.target.projections[-1], MIRField)
                    and value_class(member.type) is MIRClass.OPTIONAL):
                validate_optional_field_write(stmt)
                continue
            if (stmt.target.projections and isinstance(member := stmt.target.projections[-1], MIRField)
                    and value_class(member.type) in AGGREGATE_CLASSES):
                validate_member_write(stmt, target_type)
                continue
            if isinstance(fact, MIRTupleInitialization):
                _require(not stmt.target.projections and target.shape.owned_tuple
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
                # A temporary (a call's Optional argument) is only ever initialized.
                _require(not stmt.target.projections and (target.kind is MIRSlotKind.LOCAL or (
                    target.kind is MIRSlotKind.TEMPORARY and fact.mode is not MIRPayloadWriteMode.ASSIGN))
                         and target.shape.scalar_wrapper
                         and isinstance(value, (MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy)),
                         "payload write needs a local or temporary scalar wrapper operation")
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
                        _require(not stmt.target.projections and target.shape.is_storage
                                 and isinstance(target.storage_duration, MIRRegionId)
                                 and target.storage_duration in regions.chains[block.region]
                                 and fact.rebind_owner is None,
                                 "scoped initialization needs owning region")
                        region_initializations[target.id] = block.id
                    case MIRRecordWriteMode.INITIALIZE_ONCE | MIRRecordWriteMode.OWN_SITE:
                        _require(not stmt.target.projections and target.shape.is_storage
                                 and target.storage_duration is MIRStorageDuration.BODY and fact.rebind_owner is None,
                                 "backing write needs private body storage")
                    case MIRRecordWriteMode.IN_PLACE:
                        # An aggregate rebuilt in place: the one its holder lends.
                        _require(fact.rebind_owner == stmt.target.root
                                 and (target.shape.borrowed_aggregate or target.shape.record_payload)
                                 and stmt.target.projections == object_prefix(target.shape, payload=True),
                                 "invalid in-place rebind owner or target")
            else:
                _require(fact is None, "invalid storage write fact")
            if target.shape.owned_tuple and not stmt.target.projections:
                _require(isinstance(fact, MIRTupleInitialization), "owned tuple needs initialization fact")
            if target.record_storage is MIRRecordStorageKind.OPTIONAL and not stmt.target.projections:
                _require(isinstance(fact, MIRRecordWrite) and fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN,
                         "optional backing needs explicit assignment fact")
            if not stmt.target.projections:
                _require(target.shape.holding is not MIRHolding.ALIAS or isinstance(value, MIRUnionExtract),
                         "payload alias requires extraction")
            for operand in operands(value):
                slot_type(operand)
                _require(slots[operand].kind is not MIRSlotKind.GLOBAL or isinstance(value, MIRRead)
                         or isinstance(value, (MIRBorrow, MIRCopy)) and slots[operand].shape.owned_leaf_borrow,
                         "global value needs explicit read")
            match value:
                case MIRRangeAdvance():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.id == source.id
                             and target.type == INT32 and target.shape.is_scalar
                             and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.step) is int and value.step in (-1, 1), "invalid range induction")
                case MIRIteratorInit():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.shape.holding is MIRHolding.CURSOR
                             and source.shape.container_or_view
                             and target.type == source.type
                             and target.shape.members == with_access(source.shape.members, target.readonly)
                             and (not source.readonly or target.readonly), "iterator source or access mismatch")
                case MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    source = slots[value.source]
                    _require(source.shape.holding is MIRHolding.CURSOR
                             and not stmt.target.projections, "iterator operation needs iterator source and local target")
                    member = source.shape.element
                    match value:
                        case MIRIteratorHasNext():
                            _require(target_type == BOOL and target.shape.is_scalar,
                                     "iterator test needs bool target")
                        case MIRIteratorAdvance():
                            _require(target.id == source.id, "advance must update its iterator")
                        case MIRIteratorRead() if member.cls is MIRClass.OWNED_LEAF:
                            # An owned-leaf element is read through a borrow of it or a view.
                            _require(target.kind is not MIRSlotKind.PARAMETER and target.shape.leaf_borrow
                                     and (target.type == member.type or view_compatible(target.type, member.type)),
                                     "iterator element type or access mismatch")
                        case MIRIteratorRead():
                            # An inert leaf is read by value, a record element through a holder.
                            _require(target.type == member.type
                                     and (target.shape.is_scalar if member.cls is MIRClass.LEAF
                                          else target.shape.is_pointer)
                                     and target.kind is not MIRSlotKind.PARAMETER
                                     and (member.cls is MIRClass.LEAF
                                          or not (member.readonly or source.readonly) or target.readonly),
                                     "iterator element type or access mismatch")
                case MIRUnionConstruct() | MIRUnionCopy():
                    _require(not stmt.target.projections and target.shape.cls is MIRClass.UNION
                             and target.kind is not MIRSlotKind.PARAMETER, "union operation needs local destination")
                    target_layout = target.shape
                    if isinstance(value, MIRUnionCopy):
                        source_layout = place_info(value.source)
                        _require(source_layout.cls is MIRClass.UNION and source_layout.type == target.type,
                                 "union copy layout mismatch")
                        _require(all(a is b if a is None or b is None else member_compatible(a, b)
                                     for a, b in zip(source_layout.alternatives, target_layout.alternatives)),
                                 "union copy increases access")
                    else:
                        _require(type(value.alternative) is int
                                 and 0 <= value.alternative < len(target_layout.members),
                                 "invalid union construction alternative")
                        member = target_layout.alternatives[value.alternative]
                        _require((member is None) == (value.source is None), "union construction payload mismatch")
                        if member is not None:
                            _require(member_compatible(payload_operand(value.source), member),
                                     "union construction type or access mismatch")
                case MIRIsAlternative():
                    source_layout = place_info(value.source)
                    _require(not stmt.target.projections and target.shape.is_scalar
                             and target_type == BOOL and source_layout.cls is MIRClass.UNION,
                             "union test type mismatch")
                    _require(bool(value.alternatives) and len(set(value.alternatives)) == len(value.alternatives)
                             and all(type(i) is int and 0 <= i < len(source_layout.members)
                                     for i in value.alternatives), "invalid tested union alternatives")
                case MIRUnionExtract():
                    source = value.source
                    _require(not stmt.target.projections and len(source.projections) == 1
                             and isinstance(source.projections[0], MIRUnionPayload)
                             and target.kind is not MIRSlotKind.PARAMETER, "unsupported union extraction")
                    _require(place_type(source) == target_type, "union extraction type mismatch")
                    wrapper = shape_at(MIRPlace(source.root, source.projections[:-1]))
                    member = wrapper.alternatives[source.projections[-1].alternative]
                    if member.cls is MIRClass.LEAF:
                        _require(target.shape.holding is MIRHolding.ALIAS and target.alias_source == source,
                                 "scalar extraction must bind payload storage")
                    else:
                        _require(target.shape.is_pointer
                                 and (not member.readonly or target.readonly), "union extraction increases access")
                case MIROptionalConstruct() | MIROptionalCopy():
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.shape.cls is MIRClass.OPTIONAL
                             and target.kind is not MIRSlotKind.PARAMETER, "optional operation needs local destination")
                    target_layout = target.shape.payload
                    if isinstance(value, MIROptionalCopy):
                        wrapper = place_info(value.source)
                        _require(wrapper.cls is MIRClass.OPTIONAL and value.may_raise is False,
                                 "optional copy needs optional source")
                        member = wrapper.payload
                        if member.is_storage:
                            # A holder of an inline payload borrows it with
                            # the access of the place it lies in.
                            _require(target_layout.is_pointer,
                                     "optional payload type or access mismatch")
                            member = inline_payload_holder(wrapper, records)
                    elif value.source is not None:
                        member = payload_operand(value.source)
                    else:
                        member = target_layout
                    _require(member_compatible(member, target_layout), "optional payload type or access mismatch")
                case MIRIsPresent():
                    _require(target_type == BOOL and place_info(value.source).cls is MIRClass.OPTIONAL,
                             "presence test needs optional source and bool destination")
                case MIRTupleConstruct() | MIRTupleCopy():
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.shape.cls is MIRClass.TUPLE
                             and target.kind is not MIRSlotKind.PARAMETER,
                             "tuple operation needs tuple destination")
                    target_layout = target.shape
                    if isinstance(value, MIRTupleConstruct):
                        _require(len(value.elements) == len(target_layout.elements),
                                 "tuple payload type or access mismatch")
                        elements = []
                        for source, member in zip(value.elements, target_layout.elements):
                            if isinstance(source, MIRConstruct):
                                _require(member.is_storage,
                                         "tuple constructor needs inline record member")
                                _require(construct_members_ok(source, records[member.type].fields),
                                         "incomplete or mistyped tuple record construction")
                                elements.append(member)
                            else:
                                _require(not member.is_storage,
                                         "inline tuple member needs constructor")
                                elements.append(slots[source].shape)
                    else:
                        source = slots[value.source]
                        source_layout = source.shape
                        _require(source_layout.cls is MIRClass.TUPLE, "tuple copy needs tuple source")
                        _require(not source.shape.owned_tuple and not target.shape.owned_tuple,
                                 "owning tuple copy is unsupported")
                        elements = source_layout.elements
                    _require(len(elements) == len(target_layout.elements)
                             and all(member_compatible(src, dst)
                                     for src, dst in zip(elements, target_layout.elements)),
                             "tuple payload type or access mismatch")
                case MIRConstruct() | MIRCopy() | MIRMove() | MIRCall() if (
                        not isinstance(value, MIRCall) or isinstance(fact, MIRRecordWrite)):
                    if fact is None or fact.mode not in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.INITIALIZE_REGION,
                                                         MIRRecordWriteMode.OPTIONAL_ASSIGN, MIRRecordWriteMode.IN_PLACE):
                        owning_blocks.add(block.id)
                    target = slots[stmt.target.root]
                    _require(value_class(target_type) in AGGREGATE_CLASSES and defined(target_type) and (
                        (not stmt.target.projections and target.shape.is_storage)
                        or stmt.target.projections
                        and stmt.target.projections == object_prefix(target.shape, payload=True)),
                        "record destination type")
                    if fact is not None and fact.mode in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.OPTIONAL_ASSIGN):
                        _require(facts(target_type).movable, "reusable backing needs movable record")
                    if not stmt.target.projections:
                        _require(target.record_storage is MIRRecordStorageKind.OPTIONAL
                                 or stmt.target.root not in initialized_storage, "repeated storage initialization")
                        initialized_storage.add(stmt.target.root)
                    else:
                        _require(facts(target_type).movable
                                 and (isinstance(value, MIRConstruct)
                                      or fact is not None and fact.mode is MIRRecordWriteMode.IN_PLACE),
                                 "unsupported record replacement")
                    validate_record_value(value, target_type, stmt.target)
                case MIRBorrow():
                    target = slots[stmt.target.root]
                    source = place_info(value.source)
                    source_type, readonly = source.type, source.readonly
                    element = elements_place(value.source)
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER,
                             "storage borrow type mismatch")
                    if target.shape.container_view_holder:
                        # A view of the whole region (an unstepped slice): it
                        # never raises, and it views the region's member type.
                        _require(element and not value.may_raise
                                 and target.shape.element.type == source_type,
                                 "container view borrow type mismatch")
                    elif source.borrow_holder:
                        # A view member read: the holder takes the loan stored there.
                        _require(target.shape.view_holder and bool(value.source.projections)
                                 and isinstance(value.source.projections[-1], MIRField)
                                 and source.cls is MIRClass.VIEW and view_compatible(target_type, source_type),
                                 "storage borrow type mismatch")
                    else:
                        _require(target.shape.is_pointer
                                 and source.owned_place
                                 and (target_type == source_type
                                      or target.shape.view_holder and view_compatible(target_type, source_type)),
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
                        _require(target.shape.is_scalar
                                 and target_type == unwrap_own(signature_result(value.summary.callee.signature)),
                                 "call result type or target mismatch")
                    else:
                        _require(target.shape.borrow_holder
                                 and target_type == result.type and (not result.readonly or target.readonly),
                                 "call result type or access mismatch")
                case MIRConstant() if target.shape.leaf_borrow:
                    # A str or bytes literal lives in static storage its holder borrows.
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.value) in (str, bytes) and owned_constant(read_leaf(target), value.value),
                             "static literal needs a borrowed holder of its type")
                case MIRConstant():
                    _require(leaf_constant(target_type, value.value), "constant type or range mismatch")
                case MIRRead():
                    _require(target.shape.holding is not MIRHolding.ALIAS and target_shape.cls is MIRClass.LEAF
                             and target_type == place_type(value.source), "read type mismatch")
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                case MIRAlias():
                    target = slots[stmt.target.root]
                    source = slots[value.source]
                    _require(not stmt.target.projections
                             and target.shape.borrow_holder
                             and source.shape.holding is target.shape.holding
                             and (target.shape.is_pointer or source.shape.cls is target.shape.cls)
                             and (not target.shape.members
                                  or target.shape.members == with_access(source.shape.members, target.readonly))
                             and (target_type == source.type
                                  or target.shape.view_holder and source.shape.leaf_borrow
                                  and view_compatible(target_type, source.type)), "alias type mismatch")
                    _require(target.kind is not MIRSlotKind.PARAMETER, "reference parameter reseat")
                    _require(not source.readonly or target.readonly, "alias increases access")
                case MIRCompare():
                    _require(value.op in COMPARISON_OPS,
                             "unsupported comparison")
                    _require(target_type == BOOL, "comparison result is not bool")
                    left, right = slots[value.left], slots[value.right]
                    certified = certified_primitive_comparison(left.type, right.type)
                    _require(all(s.shape.is_scalar or s.shape.leaf_borrow for s in (left, right))
                             and (left.type == right.type or certified),
                             "comparison operand type mismatch")
                    _require(certified and primitive_operand(left) and primitive_operand(right),
                             "comparison lacks the primitive contract")
                case MIRNot():
                    _require(target_type == BOOL and slot_type(value.operand) == BOOL
                             and slots[value.operand].shape.is_scalar,
                             "not operand or result is not bool")
                case MIROp():
                    _require(not stmt.target.projections and target.shape.is_scalar
                             and target.kind is not MIRSlotKind.GLOBAL
                             and isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                             and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                             and all(slots[o].shape.is_scalar or slots[o].shape.leaf_borrow for o in value.operands),
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
                _require(slot_type(term.condition) == BOOL and slots[term.condition].shape.is_scalar,
                         "branch condition is not bool")
                _require(slots[term.condition].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            case MIRReturn():
                if term.value is None:
                    _require(isinstance(fn.return_type, VoidType), "missing return value")
                elif fn.borrowed_result is not None:
                    source = slots[term.value]
                    _require(source.shape.borrow_holder
                             and source.type == fn.borrowed_result.type
                             and (not source.readonly or fn.borrowed_result.readonly),
                             "borrowed return type or access mismatch")
                elif slots[term.value].shape.leaf_storage:
                    # An owned leaf returns by value: its storage is copied or moved out.
                    _require(slot_type(term.value) == owned_value_type(fn.return_type), "return type mismatch")
                elif owned_aggregate_result(fn.return_type) is not None:
                    # The body's own aggregate storage (a local's, or an
                    # `Own[...]` parameter's) is moved out: a readonly or
                    # non-movable one would be copied instead.
                    source = slots[term.value]
                    _require(source.shape.is_storage and (source.kind is not MIRSlotKind.PARAMETER
                                                          or handed_storage(source, fn.slot_bindings))
                             and source.type == owned_aggregate_result(fn.return_type) and not source.readonly
                             and defined(source.type) and facts(source.type).movable,
                             "owned record return needs movable owned storage")
                else:
                    _require(slot_type(term.value) == fn.return_type and slots[term.value].shape.is_scalar,
                             "return type mismatch")
                    _require(slots[term.value].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")

    exceptional = body_may_raise(fn.blocks, slots, records, fn.receiver_init)
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
            _require(all(sid in constructed for sid in reads if slots[sid].shape.scalar_wrapper),
                     "read before storage initialization")
            if isinstance(stmt, MIRAssign) and stmt.storage_write == MIRPayloadWrite(MIRPayloadWriteMode.ASSIGN):
                _require(stmt.target.root in constructed, "assignment before storage initialization")
            if (isinstance(stmt, MIRAssign) and not stmt.target.projections
                    and stmt.storage_write == MIRRecordWrite(MIRRecordWriteMode.IN_PLACE)
                    and slots[stmt.target.root].shape.leaf_storage):
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
