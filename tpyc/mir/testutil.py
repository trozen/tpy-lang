"""A bounded MIR interpreter with explicit shared object identities."""

import copy
from dataclasses import dataclass
import operator

from ..type_def_registry import int_traits_of
from ..typesys import INT32_MIN, INT32_MAX

from .nodes import (
    MIRAlias, MIRBranch, MIRCallStmt, MIRCompare, MIRConstant, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRSlotKind, MIRGlobalId,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMemberInits, MIRMove, MIRValueKind, MIRSlotId,
    MIRPayloadWrite, MIRPayloadWriteMode, MIRRecordStorageInit, MIRRecordStorageKind,
    MIRDeref, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex, MIRStorageInit, MIREdge,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionPayload, MIRUnionExtract,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance, MIROp, MIRPrint, MIRContainerElements, place_layout,
)
from .region_flow import MIRRegionFlow
from .coverage import container_view_holder, owned_container, owned_tuple
from .validate import statement_reads

# The structural writes the interpreter runs for a native method stub, by
# method name: a test oracle for the source meaning, not a contract.
_CONTAINER_METHODS = {
    "append": lambda container, value: container.elements.append(value),
    "clear": lambda container: container.elements.clear(),
}


@dataclass(frozen=True)
class Reference:
    identity: int
    path: tuple[MIRFieldId | MIRTupleIndex, ...] = ()


@dataclass(eq=False)
class ContainerValue:
    """One container object, shared by every holder of it: element writes
    and structural writes (`append`, `clear`) are visible through each."""
    elements: list[int | Reference]

    def __post_init__(self) -> None:
        self.elements = list(self.elements)


@dataclass(frozen=True)
class IteratorValue:
    source: ContainerValue
    index: int = 0


@dataclass(frozen=True)
class TupleValue:
    elements: tuple[int | bool | Reference, ...]


@dataclass(frozen=True)
class OptionalValue:
    payload: int | bool | Reference | None = None


@dataclass(frozen=True)
class UnionValue:
    alternative: int
    payload: int | bool | Reference | None = None


@dataclass(frozen=True)
class PayloadAlias:
    place: MIRPlace


Value = int | bool | Reference | TupleValue | OptionalValue | UnionValue | PayloadAlias | ContainerValue | IteratorValue
Record = dict[MIRFieldId | MIRTupleIndex, 'int | bool | Record']
Heap = dict[int, Record]

# Source-level meaning of the primitive operations a test body runs; a
# fixed-width result outside its type's range raises like a checked op.
_OPERATIONS = {
    "+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv,
    "//": operator.floordiv, "%": operator.mod, "**": operator.pow, "<<": operator.lshift,
    ">>": operator.rshift, "&": operator.and_, "|": operator.or_, "^": operator.xor,
    "__neg__": operator.neg, "__pos__": operator.pos, "__invert__": operator.invert,
}


def execute(fn: MIRFunction, *args: Value, heap: Heap | None = None,
            global_state: dict[MIRGlobalId, int | bool] | None = None,
            output: list[tuple[Value, ...]] | None = None) -> Value | None:
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert len(params) == len(args)
    values = dict(zip(params, args))
    regions = MIRRegionFlow(fn)
    objects = heap if heap is not None else {}
    blocks = {b.id: b for b in fn.blocks}
    slots = {s.id: s for s in fn.slots}
    physical: dict[MIRSlotId, Value] = {
        sid: value for sid, value in values.items()
        if slots[sid].value_kind in (MIRValueKind.OPTIONAL, MIRValueKind.UNION)}
    global_values = global_state if global_state is not None else {}
    for slot in fn.slots:
        if slot.global_id is not None:
            assert slot.global_id in global_values, "global storage must be initialized by caller"
    records = {r.type: r for r in fn.records}
    if fn.receiver_init is not None:
        init = fn.receiver_init
        reference = values[init.receiver]
        assert isinstance(reference, Reference) and not reference.path
        assert reference.identity not in objects or not objects[reference.identity]
        def member_value(member: MIRField, source: MIRSlotId | MIRConstant | MIRConstruct | MIRMemberInits) -> Value:
            # A record member is inline storage: a copy of what its parameter
            # holds (or the parameter's own storage moved in), or the fields
            # its own constructor initializes.
            layout = records.get(member.type)
            layout = None if layout is None or layout.opaque else layout
            match source:
                case MIRConstant():
                    return source.value
                case MIRMemberInits():
                    return {f.id: member_value(f, m.source) for f, m in zip(layout.fields, source.fields)}
                case MIRConstruct():
                    return ContainerValue([values[s] for s in source.fields])
            value = values[source]
            if layout is not None and isinstance(value, Reference):
                held = objects[value.identity]
                for step in value.path:
                    held = held[step]
                return copy.deepcopy(held)
            # A container member copies its parameter's container.
            return ContainerValue(value.elements) if isinstance(value, ContainerValue) else value
        objects[reference.identity] = {
            member.id: member_value(member, value.source)
            for member, value in zip(records[slots[init.receiver].type].fields, init.fields)
        }
    next_identity = max(objects, default=0) + 1
    bid = fn.entry
    comparisons = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
    # MIR names any element (`[elements]`); the lowering evaluates the index
    # into a fresh temporary just before the access, which the interpreter
    # takes as the element the source meant.
    last_index: list[int] = [0]

    def record(reference: Reference) -> Record:
        value = objects[reference.identity]
        for member in reference.path:
            value = value[member]
            assert isinstance(value, dict)
        return value

    def field(place: MIRPlace) -> tuple[Record, MIRFieldId]:
        reference = read(MIRPlace(place.root, place.projections[:-1]))
        assert isinstance(reference, Reference)
        member = place.projections[-1]
        assert isinstance(member, MIRField)
        return record(reference), member.id

    def read(place: MIRPlace) -> Value:
        identity = slots[place.root].global_id
        value = global_values[identity] if identity is not None else values[place.root]
        if isinstance(value, PayloadAlias):
            value = read(value.place)
        for projection in place.projections:
            match projection:
                case MIRUnionPayload():
                    assert isinstance(value, UnionValue) and value.alternative == projection.alternative
                    value = value.payload
                    assert value is not None
                case MIROptionalPayload():
                    assert isinstance(value, OptionalValue) and value.payload is not None
                    value = value.payload
                case MIRTupleIndex():
                    assert isinstance(value, TupleValue)
                    value = value.elements[projection.index]
                case MIRDeref():
                    assert isinstance(value, Reference)
                case MIRContainerElements():
                    assert isinstance(value, ContainerValue)
                    value = value.elements[last_index[0]]
                case _:
                    assert isinstance(projection, MIRField) and isinstance(value, Reference)
                    member = record(value)[projection.id]
                    value = (Reference(value.identity, value.path + (projection.id,))
                             if isinstance(member, dict) else member)
        return value

    for _ in range(100):
        block = blocks[bid]
        for stmt in block.statements:
            if isinstance(stmt, MIRCallStmt):
                callee = stmt.call.summary.callee
                method = _CONTAINER_METHODS.get(callee.identity.qualified_name.rsplit(".", 1)[-1])
                if not getattr(callee, "receiver", False) or method is None:
                    raise AssertionError("calls require callee execution, not summary evaluation")
                receiver, *arguments = (values[sid] for sid in stmt.call.arguments)
                method(receiver, *arguments)
                continue
            if isinstance(stmt, MIRPrint):
                printed = tuple(values[sid] for sid in stmt.arguments)
                if output is not None:
                    output.append(printed)
                continue
            if isinstance(stmt, MIRRecordStorageInit):
                physical[stmt.target.root] = OptionalValue()
                values.pop(stmt.target.root, None)
                continue
            if isinstance(stmt, MIRStorageInit):
                physical[stmt.target.root] = (OptionalValue() if slots[stmt.target.root].value_kind is MIRValueKind.OPTIONAL
                                              else UnionValue(stmt.alternative, stmt.value.value))
                values.pop(stmt.target.root, None)
                continue
            assert all(sid in values or slots[sid].global_id is not None for sid in statement_reads(stmt)), \
                "source read before assignment"
            if isinstance(stmt.storage_write, MIRPayloadWrite) and stmt.storage_write.mode is MIRPayloadWriteMode.ASSIGN:
                assert stmt.target.root in physical, "assignment before storage initialization"
            rhs = stmt.value
            if (not stmt.target.projections
                    and slots[stmt.target.root].record_storage is MIRRecordStorageKind.OPTIONAL):
                assert stmt.target.root in physical, "record assignment before wrapper initialization"
            match rhs:
                case MIRRangeAdvance():
                    value = values[rhs.source] + rhs.step
                    assert INT32_MIN <= value <= INT32_MAX, "range induction overflow"
                case MIRIteratorInit():
                    source = values[rhs.source]
                    assert isinstance(source, ContainerValue)
                    value = IteratorValue(source)
                case MIRIteratorHasNext():
                    source = values[rhs.source]
                    assert isinstance(source, IteratorValue)
                    value = source.index < len(source.source.elements)
                case MIRIteratorRead() | MIRIteratorAdvance():
                    source = values[rhs.source]
                    assert isinstance(source, IteratorValue) and source.index < len(source.source.elements)
                    value = (source.source.elements[source.index] if isinstance(rhs, MIRIteratorRead)
                             else IteratorValue(source.source, source.index + 1))
                case MIRConstant():
                    value = rhs.value
                case MIRRead():
                    value = read(rhs.source)
                case MIRAlias():
                    value = values[rhs.source]
                    assert isinstance(value, (Reference, ContainerValue))
                case MIRBorrow() if container_view_holder(slots[stmt.target.root]):
                    # A Span views the whole region of its source.
                    value = read(MIRPlace(rhs.source.root, rhs.source.projections[:-1]))
                    assert isinstance(value, ContainerValue)
                case MIRBorrow():
                    value = read(rhs.source)
                    assert isinstance(value, (Reference, ContainerValue))
                case MIRTupleConstruct():
                    target = slots[stmt.target.root]
                    elements = []
                    backing: Record = {}
                    for i, src in enumerate(rhs.elements):
                        if isinstance(src, MIRConstruct):
                            layout = records[place_layout(stmt.target, slots).elements[i].type]
                            backing[MIRTupleIndex(i)] = {f.id: values[s] for f, s in zip(layout.fields, src.fields)}
                            elements.append(Reference(next_identity, (MIRTupleIndex(i),)))
                        else:
                            elements.append(values[src])
                    if owned_tuple(target):
                        objects[next_identity] = backing
                        next_identity += 1
                    value = TupleValue(tuple(elements))
                case MIRTupleCopy():
                    source = values[rhs.source]
                    assert isinstance(source, TupleValue)
                    value = TupleValue(source.elements)
                case MIROptionalConstruct():
                    value = OptionalValue(read(rhs.source) if rhs.source is not None else None)
                case MIROptionalCopy():
                    source = read(rhs.source)
                    assert isinstance(source, OptionalValue)
                    value = OptionalValue(source.payload)
                case MIRIsPresent():
                    source = read(rhs.source)
                    assert isinstance(source, OptionalValue)
                    value = source.payload is not None
                case MIRUnionConstruct():
                    value = UnionValue(rhs.alternative, read(rhs.source) if rhs.source is not None else None)
                case MIRUnionCopy():
                    source = read(rhs.source)
                    assert isinstance(source, UnionValue)
                    value = UnionValue(source.alternative, source.payload)
                case MIRIsAlternative():
                    source = read(rhs.source)
                    assert isinstance(source, UnionValue)
                    value = source.alternative in rhs.alternatives
                case MIRUnionExtract():
                    value = (PayloadAlias(rhs.source) if slots[stmt.target.root].value_kind is MIRValueKind.PAYLOAD_ALIAS
                             else read(rhs.source))
                case MIRConstruct() if owned_container(slots[stmt.target.root]):
                    value = ContainerValue([values[src] for src in rhs.fields])
                case MIRConstruct():
                    target = slots[stmt.target.root]
                    typ = (place_layout(MIRPlace(stmt.target.root), slots).type
                           if stmt.target.projections == (MIROptionalPayload(), MIRDeref())
                           else target.container_layout.subscript.type
                           if stmt.target.projections == (MIRContainerElements(),) else target.type)
                    layout = records[typ]
                    value = {f.id: values[src] for f, src in zip(layout.fields, rhs.fields)}
                case MIRCopy() | MIRMove():
                    source = rhs.source.root if isinstance(rhs, MIRCopy) else rhs.source
                    reference = values[source]
                    assert isinstance(reference, Reference)
                    value = record(reference).copy()
                case MIRCompare():
                    value = comparisons[rhs.op](values[rhs.left], values[rhs.right])
                case MIRNot():
                    value = not values[rhs.operand]
                case MIROp():
                    value = _OPERATIONS[rhs.op](*(values[sid] for sid in rhs.operands))
                    traits = int_traits_of(slots[stmt.target.root].type)
                    if traits is not None and not traits.min_value <= value <= traits.max_value:
                        raise OverflowError(f"{rhs.op} overflows {slots[stmt.target.root].type}")
                case _:
                    raise AssertionError(rhs)
            if isinstance(value, dict):
                if stmt.target.projections:
                    reference = read(stmt.target)
                    assert isinstance(reference, Reference)
                else:
                    assert slots[stmt.target.root].value_kind is MIRValueKind.OWNED
                    if stmt.target.root in values:
                        # Validation admits repeated root writes only for reusable backing.
                        reference = values[stmt.target.root]
                        assert isinstance(reference, Reference)
                    else:
                        reference = Reference(next_identity)
                        next_identity += 1
                        values[stmt.target.root] = reference
                destination = record(reference) if stmt.target.projections else None
                if destination is not None:
                    destination.clear()
                    destination.update(value)
                else:
                    objects[reference.identity] = value
            elif stmt.target.projections and isinstance(stmt.target.projections[-1], MIRContainerElements):
                container = read(MIRPlace(stmt.target.root, stmt.target.projections[:-1]))
                assert isinstance(container, ContainerValue)
                container.elements[last_index[0]] = value
            elif stmt.target.projections:
                obj, member = field(stmt.target)
                assert not isinstance(value, Reference)
                obj[member] = value
            else:
                identity = slots[stmt.target.root].global_id
                if identity is not None:
                    assert type(value) in (int, bool)
                    global_values[identity] = value
                else:
                    values[stmt.target.root] = value
                    if slots[stmt.target.root].value_kind in (MIRValueKind.OPTIONAL, MIRValueKind.UNION):
                        physical[stmt.target.root] = value
                    if (slots[stmt.target.root].kind is MIRSlotKind.TEMPORARY and type(value) is int
                            and slots[stmt.target.root].value_kind is MIRValueKind.SCALAR):
                        last_index[0] = value
        term = block.terminator
        match term:
            case MIRReturn(value=result):
                return values[result] if result is not None else None
            case MIRBranch(condition=condition, then=then, otherwise=otherwise):
                edge = MIREdge(bid, int(not values[condition]))
                bid = then if values[condition] else otherwise
            case MIRGoto(target=target):
                edge = MIREdge(bid)
                bid = target
            case _:
                raise AssertionError(term)
        for sid in regions.edges[edge].reset:
            values.pop(sid, None)
        for sid in regions.edges[edge].ended:
            physical.pop(sid, None)
    raise AssertionError("unexpected nontermination")
