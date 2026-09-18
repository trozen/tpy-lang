"""A bounded MIR interpreter with explicit shared object identities."""

from dataclasses import dataclass
import operator

from .nodes import (
    MIRAlias, MIRBranch, MIRCompare, MIRConstant, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRSlotKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRValueKind,
    MIRDeref, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalPayload,
    MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionPayload, MIRUnionExtract,
)


@dataclass(frozen=True)
class Reference:
    identity: int
    path: tuple[MIRFieldId, ...] = ()


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


Value = int | bool | Reference | TupleValue | OptionalValue | UnionValue | PayloadAlias
Record = dict[MIRFieldId, 'int | bool | Record']
Heap = dict[int, Record]


def execute(fn: MIRFunction, *args: Value, heap: Heap | None = None) -> Value | None:
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert len(params) == len(args)
    values = dict(zip(params, args))
    objects = heap if heap is not None else {}
    blocks = {b.id: b for b in fn.blocks}
    slots = {s.id: s for s in fn.slots}
    records = {r.type: r for r in fn.records}
    if fn.receiver_init is not None:
        init = fn.receiver_init
        reference = values[init.receiver]
        assert isinstance(reference, Reference) and not reference.path
        assert reference.identity not in objects or not objects[reference.identity]
        objects[reference.identity] = {
            member.id: value.value if isinstance(value, MIRConstant) else values[value]
            for member, value in zip(records[slots[init.receiver].type].fields, init.fields)
        }
    next_identity = max(objects, default=0) + 1
    bid = fn.entry
    comparisons = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}

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
        value = values[place.root]
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
                case _:
                    assert isinstance(projection, MIRField) and isinstance(value, Reference)
                    member = record(value)[projection.id]
                    value = (Reference(value.identity, value.path + (projection.id,))
                             if isinstance(member, dict) else member)
        return value

    for _ in range(100):
        block = blocks[bid]
        for stmt in block.statements:
            rhs = stmt.value
            match rhs:
                case MIRConstant():
                    value = rhs.value
                case MIRRead():
                    value = read(rhs.source)
                case MIRAlias():
                    value = values[rhs.source]
                    assert isinstance(value, Reference)
                case MIRBorrow():
                    value = read(rhs.source)
                    assert isinstance(value, Reference)
                case MIRTupleConstruct():
                    value = TupleValue(tuple(values[src] for src in rhs.elements))
                case MIRTupleCopy():
                    source = values[rhs.source]
                    assert isinstance(source, TupleValue)
                    value = TupleValue(source.elements)
                case MIROptionalConstruct():
                    value = OptionalValue(values[rhs.source] if rhs.source is not None else None)
                case MIROptionalCopy():
                    source = values[rhs.source]
                    assert isinstance(source, OptionalValue)
                    value = OptionalValue(source.payload)
                case MIRIsPresent():
                    source = values[rhs.source]
                    assert isinstance(source, OptionalValue)
                    value = source.payload is not None
                case MIRUnionConstruct():
                    value = UnionValue(rhs.alternative, values[rhs.source] if rhs.source is not None else None)
                case MIRUnionCopy():
                    source = values[rhs.source]
                    assert isinstance(source, UnionValue)
                    value = UnionValue(source.alternative, source.payload)
                case MIRIsAlternative():
                    source = values[rhs.source]
                    assert isinstance(source, UnionValue)
                    value = source.alternative in rhs.alternatives
                case MIRUnionExtract():
                    value = (PayloadAlias(rhs.source) if slots[stmt.target.root].value_kind is MIRValueKind.PAYLOAD_ALIAS
                             else read(rhs.source))
                case MIRConstruct():
                    layout = records[slots[stmt.target.root].type]
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
                case _:
                    raise AssertionError(rhs)
            if isinstance(value, dict):
                if stmt.target.projections:
                    reference = values[stmt.target.root]
                    assert isinstance(reference, Reference)
                else:
                    assert slots[stmt.target.root].value_kind is MIRValueKind.RECORD_STORAGE
                    reference = Reference(next_identity)
                    next_identity += 1
                    values[stmt.target.root] = reference
                destination = record(reference) if stmt.target.projections else None
                if destination is not None:
                    destination.clear()
                    destination.update(value)
                else:
                    objects[reference.identity] = value
            elif stmt.target.projections:
                obj, member = field(stmt.target)
                assert not isinstance(value, Reference)
                obj[member] = value
            else:
                values[stmt.target.root] = value
        term = block.terminator
        match term:
            case MIRReturn(value=result):
                return values[result] if result is not None else None
            case MIRBranch(condition=condition, then=then, otherwise=otherwise):
                bid = then if values[condition] else otherwise
            case MIRGoto(target=target):
                bid = target
            case _:
                raise AssertionError(term)
    raise AssertionError("unexpected nontermination")
