"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque
from dataclasses import dataclass

from ..thir.nodes import Form
from ..typesys import (
    BOOL, INT32, INT32_MAX, INT32_MIN, NominalType, OptionalType, ReadonlyType, TupleType, TpyType, VoidType,
    UnionType, is_void_like_type, unwrap_readonly,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFieldId, MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRRvalue, MIRSlotId, MIRSlotKind, MIRValueKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRReceiverInit, MIRBodyKind, MIRGlobalId,
    MIRRecordWrite, MIRRecordWriteMode, MIRPayloadWrite, MIRPayloadWriteMode, MIRStorageInit, MIRRecordStorageInit, MIRStatement,
    MIRRegionId, MIREdge, MIRRecordStorageKind, MIRTupleInitialization,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRUnionLayout, MIRUnionPayload, MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract,
)
from .presence import MIRPresence, _analyze_presence
from .coverage import owned_tuple, scalar_wrapper
from .region_flow import MIRRegionFlow, outgoing_edges


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


class MIRPresenceError(MIRValidationError):
    """A payload access lacks a valid presence proof at its CFG position."""


class MIRDefiniteAssignmentError(MIRValidationError):
    """Structural CFG paths do not establish initialization before a read."""


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
        elif slot.storage_duration is MIRStorageDuration.BODY and regions:
            _require(slot.residence == roots[0].id, "body storage has nested residence")
    flow = MIRRegionFlow(fn)
    for transition in flow.edges.values():
        for rid in transition.entered:
            _require(regions[rid].entry == transition.target, "entry into region interior")
    return flow


def operands(value: MIRRvalue) -> tuple[MIRSlotId, ...]:
    match value:
        case (MIRRead(source=source) | MIRCopy(source=source)
              | MIRUnionExtract(source=source) | MIRBorrow(source=source)):
            _require(isinstance(source, MIRPlace), "invalid read place")
            return (source.root,)
        case (MIRAlias(source=source) | MIRMove(source=source)
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
        case MIRStorageInit() | MIRRecordStorageInit():
            return ()
        case MIRAssign(target=target, value=value):
            return (*operands(value), target.root) if target.projections else operands(value)
        case _:
            raise MIRValidationError("unknown instruction")


def source_definition(stmt: MIRStatement) -> MIRSlotId | None:
    match stmt:
        case MIRStorageInit() | MIRRecordStorageInit():
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
    _require(fn.kind in (MIRBodyKind.FREE_FUNCTION, MIRBodyKind.METHOD, MIRBodyKind.CONSTRUCTOR),
             "unsupported body kind")
    _require((fn.kind is MIRBodyKind.CONSTRUCTOR) == (fn.receiver_init is not None),
             "constructor entry mismatch")
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(fn.return_type in (INT32, BOOL) or isinstance(fn.return_type, VoidType),
             "unsupported return type")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    regions = _region_structure(fn)
    records = {r.type: r for r in fn.records}
    _require(len(records) == len(fn.records), "duplicate record layout")
    field_types: dict[MIRFieldId, TpyType] = {}
    for record in fn.records:
        _require(isinstance(record.type, NominalType) and record.type.qualified_name() is not None
                 and record.type not in (BOOL, INT32) and not record.type.type_args
                 and not record.type.is_protocol, "unsupported record layout identity")
        _require(type(record.copyable) is bool and type(record.movable) is bool,
                 "invalid record eligibility")
        seen: set[MIRFieldId] = set()
        for member in record.fields:
            _require(isinstance(member, MIRField) and isinstance(member.id, MIRFieldId)
                     and member.id.owner == record.type and bool(member.id.name)
                     and member.id not in seen and member.type in (BOOL, INT32),
                     "invalid record layout field")
            seen.add(member.id)
            field_types[member.id] = member.type
    global_ids: set[MIRGlobalId] = set()
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        _require(isinstance(slot.record_storage, MIRRecordStorageKind), "invalid record storage kind")
        if slot.record_storage is MIRRecordStorageKind.OPTIONAL:
            _require(slot.value_kind is MIRValueKind.RECORD_STORAGE
                     and slot.kind is MIRSlotKind.LOCAL
                     and not slot.readonly and slot.storage_duration is not None,
                     "optional backing needs local record storage")
        if slot.storage_duration is not None:
            _require(isinstance(slot.storage_duration, (MIRStorageDuration, MIRRegionId))
                     and (slot.value_kind is MIRValueKind.RECORD_STORAGE or scalar_wrapper(slot) or owned_tuple(slot)
                          or slot.value_kind is MIRValueKind.UNION)
                     and ((slot.storage_duration is MIRStorageDuration.CALLER
                           and slot.kind is MIRSlotKind.PARAMETER and slot.value_kind is MIRValueKind.UNION)
                          or ((slot.storage_duration is MIRStorageDuration.BODY
                               or isinstance(slot.storage_duration, MIRRegionId))
                              and slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY))),
                     "invalid storage duration fact")
        if slot.kind is MIRSlotKind.GLOBAL:
            _require(isinstance(slot.global_id, MIRGlobalId) and bool(slot.global_id.module and slot.global_id.name)
                     and slot.global_id not in global_ids and slot.value_kind is MIRValueKind.SCALAR,
                     "invalid or duplicate global identity")
            global_ids.add(slot.global_id)
        else:
            _require(slot.global_id is None, "global identity on local slot")
        if slot.value_kind is MIRValueKind.UNION:
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
                    _require(member.type in (BOOL, INT32) and not member.readonly, "unsupported union scalar")
                else:
                    _require(member.kind is MIRValueKind.BORROWED_RECORD
                             and isinstance(member.type, NominalType) and member.type.qualified_name() is not None
                             and not member.type.type_args and not member.type.is_protocol
                             and member.type not in (BOOL, INT32)
                             and (typ == member.type or member.readonly), "unsupported union reference")
            _require(len(kinds) == 1 and len(layout.elements) >= 2, "mixed or empty union layout")
            _require(slot.storage_duration is not MIRStorageDuration.CALLER
                     or kinds == {MIRValueKind.SCALAR}, "caller duration requires borrowed scalar union wrapper")
        elif slot.value_kind is MIRValueKind.PAYLOAD_ALIAS:
            _require(slot.type in (BOOL, INT32) and slot.form is Form.BORROW and slot.readonly
                     and slot.kind is not MIRSlotKind.PARAMETER
                     and isinstance(slot.alias_source, MIRPlace), "unsupported payload alias")
        elif slot.value_kind is MIRValueKind.OPTIONAL:
            layout = slot.optional_layout
            _require(isinstance(slot.type, OptionalType) and not slot.type.force_pointer_repr
                     and isinstance(layout, MIROptionalLayout) and slot.form is Form.VALUE
                     and not slot.readonly and type(layout.readonly) is bool, "unsupported optional slot")
            _require(unwrap_readonly(slot.type.inner) == layout.type, "optional payload type mismatch")
            if layout.kind is MIRValueKind.SCALAR:
                _require(layout.type in (BOOL, INT32) and not layout.readonly, "unsupported optional scalar")
            else:
                _require(layout.kind is MIRValueKind.BORROWED_RECORD
                         and isinstance(layout.type, NominalType) and layout.type.qualified_name() is not None
                         and layout.type not in (BOOL, INT32) and not layout.type.type_args
                         and not layout.type.is_protocol
                         and (slot.type.inner == layout.type or layout.readonly), "unsupported optional reference")
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
                    _require(member.type in (BOOL, INT32) and typ == member.type and not member.readonly,
                             "invalid tuple scalar")
                elif member.kind is MIRValueKind.RECORD_STORAGE:
                    _require(member.type in records and unwrap_readonly(typ) == member.type
                             and (typ == member.type or member.readonly), "invalid owned tuple record")
                else:
                    _require(member.kind is MIRValueKind.BORROWED_RECORD
                             and isinstance(member.type, NominalType)
                             and member.type.qualified_name() is not None
                             and member.type not in (BOOL, INT32) and not member.type.type_args
                             and not member.type.is_protocol and unwrap_readonly(typ) == member.type
                             and (typ == member.type or member.readonly), "invalid tuple reference")
        elif slot.value_kind is MIRValueKind.SCALAR:
            _require(slot.type in (INT32, BOOL) and slot.form is Form.VALUE
                     and (not slot.readonly or slot.kind is MIRSlotKind.GLOBAL),
                     "unsupported slot type or form")
        elif slot.value_kind is MIRValueKind.RECORD_STORAGE:
            _require(slot.type in records and slot.form is Form.STORAGE
                     and slot.kind is not MIRSlotKind.PARAMETER,
                     "unsupported record storage type or form")
        else:
            _require(slot.value_kind is MIRValueKind.BORROWED_RECORD
                     and isinstance(slot.type, NominalType) and slot.type.qualified_name() is not None
                     and slot.type not in (BOOL, INT32)
                     and not slot.type.type_args and not slot.type.is_protocol
                     and slot.form is Form.BORROW, "unsupported reference slot type or form")
        _require(type(slot.readonly) is bool, "invalid access capability")
        _require(slot.value_kind is MIRValueKind.TUPLE or slot.tuple_layout is None,
                 "tuple layout on non-tuple slot")
        _require(slot.value_kind is MIRValueKind.OPTIONAL or slot.optional_layout is None,
                 "optional layout on non-optional slot")
        _require(slot.value_kind is MIRValueKind.UNION or slot.union_layout is None,
                 "union layout on non-union slot")
        _require(slot.value_kind is MIRValueKind.PAYLOAD_ALIAS or slot.alias_source is None,
                 "payload alias source on non-alias slot")
        _require(isinstance(slot.kind, MIRSlotKind), "invalid slot kind")

    if fn.receiver_init is not None:
        init = fn.receiver_init
        _require(isinstance(init, MIRReceiverInit) and init.receiver in slots,
                 "invalid constructor receiver")
        receiver = slots[init.receiver]
        _require(receiver.kind is MIRSlotKind.PARAMETER
                 and receiver.value_kind is MIRValueKind.BORROWED_RECORD
                 and not receiver.readonly and receiver.type in records
                 and isinstance(fn.return_type, VoidType), "invalid constructor receiver")
        members = records[receiver.type].fields
        _require(len(init.fields) == len(members), "incomplete receiver initialization")
        for value, member in zip(init.fields, members):
            match value:
                case MIRSlotId():
                    _require(value in slots and slots[value].kind is MIRSlotKind.PARAMETER
                             and slots[value].value_kind is MIRValueKind.SCALAR
                             and slots[value].type == member.type, "invalid receiver initializer parameter")
                case MIRConstant(value=literal):
                    _require((member.type == BOOL and type(literal) is bool)
                             or (member.type == INT32 and type(literal) is int
                                 and INT32_MIN <= literal <= INT32_MAX), "invalid receiver initializer constant")
                case _:
                    raise MIRValidationError("invalid receiver initializer")

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
        inline_record = False
        for projection in place.projections:
            match projection:
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
                    _require(kind is MIRValueKind.BORROWED_RECORD, "dereference needs reference holder")
                    _require(not (write and readonly), "store through readonly reference")
                    kind = MIRValueKind.RECORD_STORAGE
                    tuple_member = False
                    optional_member = False
                case MIRField():
                    _require(kind is MIRValueKind.RECORD_STORAGE, "field needs record storage")
                    tuple_member = False
                    _require(not (write and readonly), "store through readonly storage")
                    _require(isinstance(projection.id, MIRFieldId) and projection.id.owner == typ
                             and bool(projection.id.name), "field owner mismatch")
                    member_type = unwrap_readonly(projection.type)
                    inline_record = (isinstance(member_type, NominalType) and member_type not in (BOOL, INT32)
                                     and member_type.qualified_name() is not None
                                     and not member_type.type_args and not member_type.is_protocol)
                    _require(projection.type in (BOOL, INT32) or inline_record, "unsupported field type")
                    if typ in records:
                        _require(projection.id in field_types, "field missing from record layout")
                    _require(field_types.setdefault(projection.id, projection.type) == projection.type,
                             "inconsistent field type")
                    typ = member_type
                    kind = MIRValueKind.RECORD_STORAGE if inline_record else MIRValueKind.SCALAR
                    readonly = readonly or isinstance(projection.type, ReadonlyType)
                case _:
                    raise MIRValidationError("unsupported place projections")
        _require(not (write and tuple_member), "tuple element replacement is forbidden")
        _require(not (write and optional_member), "optional payload replacement is forbidden")
        _require(not (write and inline_record), "inline record replacement is unsupported")
        if kind is MIRValueKind.RECORD_STORAGE and not place.projections:
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

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    initialized_storage: set[MIRSlotId] = set()
    owning_blocks: set[MIRBlockId] = set()
    payload_initializations: set[MIRSlotId] = set()
    payload_init_blocks: set[MIRBlockId] = set()
    region_initializations: dict[MIRSlotId, MIRBlockId] = {}
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, (MIRAssign, MIRStorageInit, MIRRecordStorageInit)), "unknown instruction")
            target_type = place_type(stmt.target, write=True)
            target = slots[stmt.target.root]
            if fn.regions:
                active = regions.chains[block.region]
                for sid in (*statement_reads(stmt), stmt.target.root):
                    _require(sid in slots, "unknown slot ID")
                    residence = slots[sid].residence
                    _require(residence is None or residence in active, "use outside binding residence")
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
                        expected = None if default_type is None else False if default_type == BOOL else 0
                        _require(type(stmt.value.value) is type(expected) and stmt.value.value == expected,
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
                _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove)), "record write on non-record operation")
                match fact.mode:
                    case MIRRecordWriteMode.OPTIONAL_ASSIGN:
                        _require(not stmt.target.projections
                                 and target.record_storage is MIRRecordStorageKind.OPTIONAL
                                 and isinstance(value, MIRConstruct) and fact.rebind_owner is None,
                                 "optional backing assignment needs constructor")
                    case MIRRecordWriteMode.INITIALIZE_REGION:
                        _require(not stmt.target.projections and target.value_kind is MIRValueKind.RECORD_STORAGE
                                 and isinstance(target.storage_duration, MIRRegionId)
                                 and target.storage_duration == block.region and fact.rebind_owner is None,
                                 "scoped initialization needs owning region")
                        region_initializations[target.id] = block.id
                    case MIRRecordWriteMode.INITIALIZE_ONCE | MIRRecordWriteMode.OWN_SITE:
                        _require(not stmt.target.projections and target.value_kind is MIRValueKind.RECORD_STORAGE
                                 and target.storage_duration is MIRStorageDuration.BODY and fact.rebind_owner is None,
                                 "backing write needs private body storage")
                        if fact.mode is MIRRecordWriteMode.OWN_SITE:
                            _require(isinstance(value, MIRConstruct), "reusable backing needs constructor")
                    case MIRRecordWriteMode.IN_PLACE:
                        _require(isinstance(value, MIRConstruct) and fact.rebind_owner == stmt.target.root
                                 and ((target.value_kind is MIRValueKind.BORROWED_RECORD
                                       and stmt.target.projections == (MIRDeref(),))
                                      or (target.value_kind is MIRValueKind.OPTIONAL
                                          and stmt.target.projections == (MIROptionalPayload(), MIRDeref()))),
                                 "invalid in-place rebind owner or target")
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
                _require(slots[operand].kind is not MIRSlotKind.GLOBAL or isinstance(value, MIRRead),
                         "global value needs explicit read")
            match value:
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
                        _require(target.value_kind is MIRValueKind.BORROWED_RECORD
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
                                _require(member.kind is MIRValueKind.RECORD_STORAGE,
                                         "tuple constructor needs inline record member")
                                fields = records[member.type].fields
                                _require(len(source.fields) == len(fields) and all(
                                    slot_type(s) == f.type and slots[s].value_kind is MIRValueKind.SCALAR
                                    for s, f in zip(source.fields, fields)), "incomplete or mistyped tuple record construction")
                                elements.append(member)
                            else:
                                _require(member.kind is not MIRValueKind.RECORD_STORAGE,
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
                case MIRConstruct() | MIRCopy() | MIRMove():
                    if fact is None or fact.mode not in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.INITIALIZE_REGION,
                                                         MIRRecordWriteMode.OPTIONAL_ASSIGN):
                        owning_blocks.add(block.id)
                    if (fact is not None and fact.mode is MIRRecordWriteMode.INITIALIZE_REGION
                            and isinstance(value, (MIRCopy, MIRMove))):
                        owning_blocks.add(block.id)
                    target = slots[stmt.target.root]
                    _require(target_type in records and (
                        (not stmt.target.projections and target.value_kind is MIRValueKind.RECORD_STORAGE)
                        or stmt.target.projections in ((MIRDeref(),), (MIROptionalPayload(), MIRDeref()))),
                        "record destination type")
                    if fact is not None and fact.mode in (MIRRecordWriteMode.OWN_SITE, MIRRecordWriteMode.OPTIONAL_ASSIGN):
                        _require(records[target_type].movable, "reusable backing needs movable record")
                    if not stmt.target.projections:
                        _require(target.record_storage is MIRRecordStorageKind.OPTIONAL
                                 or stmt.target.root not in initialized_storage, "repeated storage initialization")
                        initialized_storage.add(stmt.target.root)
                    else:
                        _require(isinstance(value, MIRConstruct) and records[target_type].movable,
                                 "unsupported record replacement")
                    match value:
                        case MIRConstruct():
                            members = records[target_type].fields
                            _require(len(value.fields) == len(members) and all(
                                slot_type(src) == member.type and slots[src].value_kind is MIRValueKind.SCALAR
                                for src, member in zip(value.fields, members)),
                                "incomplete or mistyped record construction")
                        case MIRCopy():
                            _require(records[target_type].copyable and place_type(value.source) == target_type
                                     and (value.source.projections == (MIRDeref(),)
                                          or (not value.source.projections
                                              and slots[value.source.root].value_kind is MIRValueKind.RECORD_STORAGE)),
                                     "record copy source or eligibility")
                        case _:
                            source = slots[value.source]
                            _require(source.value_kind is MIRValueKind.RECORD_STORAGE
                                     and source.type == target_type and not source.readonly
                                     and records[target_type].movable and value.source != stmt.target.root,
                                     "record move source or eligibility")
                case MIRBorrow():
                    target = slots[stmt.target.root]
                    source_type, source_kind, readonly = place_info(value.source)
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.BORROWED_RECORD
                             and target.kind is not MIRSlotKind.PARAMETER
                             and source_kind is MIRValueKind.RECORD_STORAGE
                             and target_type == source_type, "storage borrow type mismatch")
                    _require(not readonly or target.readonly, "borrow increases access")
                case MIRConstant():
                    _require((target_type == BOOL and type(value.value) is bool)
                             or (target_type == INT32 and type(value.value) is int
                                 and INT32_MIN <= value.value <= INT32_MAX),
                             "constant type or range mismatch")
                case MIRRead():
                    _require(target.value_kind is not MIRValueKind.PAYLOAD_ALIAS and target_type in (BOOL, INT32)
                             and target_type == place_type(value.source), "read type mismatch")
                case MIRAlias():
                    target = slots[stmt.target.root]
                    source = slots[value.source]
                    _require(not stmt.target.projections
                             and target.value_kind is MIRValueKind.BORROWED_RECORD
                             and source.value_kind is MIRValueKind.BORROWED_RECORD
                             and target_type == source.type, "alias type mismatch")
                    _require(target.kind is not MIRSlotKind.PARAMETER, "reference parameter reseat")
                    _require(not source.readonly or target.readonly, "alias increases access")
                case MIRCompare():
                    _require(value.op in ("==", "!=", "<", "<=", ">", ">="),
                             "unsupported comparison")
                    _require(target_type == BOOL, "comparison result is not bool")
                    _require(slot_type(value.left) in (BOOL, INT32)
                             and slot_type(value.left) == slot_type(value.right)
                             and slots[value.left].value_kind is MIRValueKind.SCALAR
                             and slots[value.right].value_kind is MIRValueKind.SCALAR,
                             "comparison operand type mismatch")
                case MIRNot():
                    _require(target_type == BOOL and slot_type(value.operand) == BOOL
                             and slots[value.operand].value_kind is MIRValueKind.SCALAR,
                             "not operand or result is not bool")
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
                else:
                    _require(slot_type(term.value) == fn.return_type and slots[term.value].value_kind is MIRValueKind.SCALAR,
                             "return type mismatch")
                    _require(slots[term.value].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")

    cyclic = _cyclic_blocks(blocks, pred)
    _require(not owning_blocks.intersection(cyclic), "owning operation in cycle")
    _require(not payload_init_blocks.intersection(cyclic), "payload initialization in cycle")
    for owner, initialization in {(slots[sid].storage_duration, bid)
                                  for sid, bid in region_initializations.items()}:
        pending = list(successors(blocks[initialization].terminator))
        seen: set[MIRBlockId] = set()
        while pending:
            bid = pending.pop()
            if owner not in regions.chains[blocks[bid].region] or bid in seen:
                continue
            _require(bid != initialization, "repeated initialization within region activation")
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
    physical_writes = {bid: {s.target.root for s in blocks[bid].statements if not s.target.projections
                             and (slots[s.target.root].record_storage is not MIRRecordStorageKind.OPTIONAL
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
                    and slots[stmt.target.root].record_storage is MIRRecordStorageKind.OPTIONAL):
                _require(stmt.target.root in constructed, "record assignment before wrapper initialization")
            if (definition := source_definition(stmt)) is not None:
                assigned.add(definition)
            if not stmt.target.projections and (slots[stmt.target.root].record_storage is not MIRRecordStorageKind.OPTIONAL
                                                or isinstance(stmt, MIRRecordStorageInit)):
                constructed.add(stmt.target.root)
        term = block.terminator
        match term:
            case MIRBranch():
                if term.condition not in assigned:
                    raise MIRDefiniteAssignmentError("branch before definite assignment")
            case MIRReturn() if term.value is not None:
                if term.value not in assigned:
                    raise MIRDefiniteAssignmentError("return before definite assignment")
