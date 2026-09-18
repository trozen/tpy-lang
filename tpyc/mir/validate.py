"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque

from ..thir.nodes import Form
from ..typesys import (
    BOOL, INT32, INT32_MAX, INT32_MIN, NominalType, OptionalType, ReadonlyType, TupleType, TpyType, VoidType,
    UnionType, is_void_like_type, unwrap_readonly,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFieldId, MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRRvalue, MIRSlotId, MIRSlotKind, MIRValueKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRUnionLayout, MIRUnionPayload, MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract,
)
from .presence import presence_error


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


class MIRPresenceError(MIRValidationError):
    """A payload access lacks a valid presence proof at its CFG position."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


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
            return elements
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


def validate_function(fn: MIRFunction) -> None:
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(fn.return_type in (INT32, BOOL) or isinstance(fn.return_type, VoidType),
             "unsupported return type")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
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
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
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
                     and slot.form is Form.VALUE and not slot.readonly
                     and slot.kind is not MIRSlotKind.PARAMETER, "unsupported tuple slot")
            _require(len(layout.elements) == len(slot.type.element_types), "tuple layout arity")
            for member, typ in zip(layout.elements, slot.type.element_types):
                _require(isinstance(member, MIRTupleElement) and type(member.readonly) is bool,
                         "invalid tuple element")
                if member.kind is MIRValueKind.SCALAR:
                    _require(member.type in (BOOL, INT32) and typ == member.type and not member.readonly,
                             "invalid tuple scalar")
                else:
                    _require(member.kind is MIRValueKind.BORROWED_RECORD
                             and isinstance(member.type, NominalType)
                             and member.type.qualified_name() is not None
                             and member.type not in (BOOL, INT32) and not member.type.type_args
                             and not member.type.is_protocol and unwrap_readonly(typ) == member.type
                             and (typ == member.type or member.readonly), "invalid tuple reference")
        elif slot.value_kind is MIRValueKind.SCALAR:
            _require(slot.type in (INT32, BOOL) and slot.form is Form.VALUE and not slot.readonly,
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

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    def place_info(place: MIRPlace, *, write: bool = False) -> tuple[TpyType, MIRValueKind, bool]:
        _require(isinstance(place, MIRPlace), "invalid place")
        typ = slot_type(place.root)
        slot = slots[place.root]
        kind, readonly = slot.value_kind, slot.readonly
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
        if kind is MIRValueKind.RECORD_STORAGE and not inline_record:
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
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, MIRAssign), "unknown instruction")
            target_type = place_type(stmt.target, write=True)
            value = stmt.value
            target = slots[stmt.target.root]
            if not stmt.target.projections:
                _require(target.value_kind is not MIRValueKind.PAYLOAD_ALIAS or isinstance(value, MIRUnionExtract),
                         "payload alias requires extraction")
            for operand in operands(value):
                slot_type(operand)
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
                    _require(not stmt.target.projections and target.value_kind is MIRValueKind.TUPLE,
                             "tuple operation needs tuple destination")
                    if isinstance(value, MIRTupleConstruct):
                        elements = tuple(MIRTupleElement(slots[s].type, slots[s].value_kind, slots[s].readonly)
                                         for s in value.elements)
                    else:
                        source = slots[value.source]
                        _require(source.value_kind is MIRValueKind.TUPLE, "tuple copy needs tuple source")
                        elements = source.tuple_layout.elements
                    _require(len(elements) == len(target.tuple_layout.elements)
                             and all(compatible_element(src, dst)
                                     for src, dst in zip(elements, target.tuple_layout.elements)),
                             "tuple payload type or access mismatch")
                case MIRConstruct() | MIRCopy() | MIRMove():
                    owning_blocks.add(block.id)
                    target = slots[stmt.target.root]
                    _require(target_type in records and (
                        (not stmt.target.projections and target.value_kind is MIRValueKind.RECORD_STORAGE)
                        or stmt.target.projections == (MIRDeref(),)), "record destination type")
                    if not stmt.target.projections:
                        _require(stmt.target.root not in initialized_storage, "repeated storage initialization")
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
        for successor in successors(term):
            _require(successor in blocks, "invalid block target")
            pred[successor].add(block.id)
        match term:
            case MIRBranch():
                _require(slot_type(term.condition) == BOOL and slots[term.condition].value_kind is MIRValueKind.SCALAR,
                         "branch condition is not bool")
            case MIRReturn():
                if term.value is None:
                    _require(isinstance(fn.return_type, VoidType), "missing return value")
                else:
                    _require(slot_type(term.value) == fn.return_type and slots[term.value].value_kind is MIRValueKind.SCALAR,
                             "return type mismatch")

    _require(not owning_blocks.intersection(_cyclic_blocks(blocks, pred)), "owning operation in cycle")

    reachable: set[MIRBlockId] = set()
    pending = [fn.entry]
    while pending:
        bid = pending.pop()
        if bid not in reachable:
            reachable.add(bid)
            pending.extend(successors(blocks[bid].terminator))
    parameters = {s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    writes = {bid: {s.target.root for s in blocks[bid].statements if not s.target.projections}
              for bid in reachable}
    # Intersection is a must analysis; initialize at top, with a synthetic
    # parameter-only incoming edge at entry even if the entry has a back edge.
    incoming = {bid: set(slots) for bid in reachable}
    outgoing = {bid: set(slots) for bid in reachable}
    work = deque(reachable)
    queued = set(reachable)
    while work:
        bid = work.popleft()
        queued.remove(bid)
        sources = [outgoing[p] for p in pred[bid] if p in reachable]
        if bid == fn.entry:
            sources.append(parameters)
        new_in = set.intersection(*sources)
        new_out = new_in | writes[bid]
        incoming[bid] = new_in
        if new_out != outgoing[bid]:
            outgoing[bid] = new_out
            for target in successors(blocks[bid].terminator):
                if target not in queued:
                    work.append(target)
                    queued.add(target)
    for bid in reachable:
        assigned = incoming[bid].copy()
        block = blocks[bid]
        for stmt in block.statements:
            reads = set(operands(stmt.value))
            if stmt.target.projections:
                reads.add(stmt.target.root)
            _require(reads <= assigned, "read before definite assignment")
            if not stmt.target.projections:
                assigned.add(stmt.target.root)
        term = block.terminator
        match term:
            case MIRBranch():
                _require(term.condition in assigned, "branch before definite assignment")
            case MIRReturn() if term.value is not None:
                _require(term.value in assigned, "return before definite assignment")
    failure = presence_error(fn)
    if failure is not None:
        raise MIRPresenceError(failure)
