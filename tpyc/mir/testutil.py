"""A bounded MIR interpreter with explicit shared object identities."""

from dataclasses import dataclass
import operator

from .nodes import (
    MIRAlias, MIRBranch, MIRCompare, MIRConstant, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRSlotKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRValueKind,
    MIRDeref, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex,
)


@dataclass(frozen=True)
class Reference:
    identity: int


@dataclass(frozen=True)
class TupleValue:
    elements: tuple[int | bool | Reference, ...]


Value = int | bool | Reference | TupleValue
Heap = dict[int, dict[MIRFieldId, int | bool]]


def execute(fn: MIRFunction, *args: Value, heap: Heap | None = None) -> Value | None:
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert len(params) == len(args)
    values = dict(zip(params, args))
    objects = heap if heap is not None else {}
    blocks = {b.id: b for b in fn.blocks}
    slots = {s.id: s for s in fn.slots}
    records = {r.type: r for r in fn.records}
    next_identity = max(objects, default=0) + 1
    bid = fn.entry
    comparisons = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}

    def field(place: MIRPlace) -> tuple[dict[MIRFieldId, int | bool], MIRFieldId]:
        reference = read(MIRPlace(place.root, place.projections[:-1]))
        assert isinstance(reference, Reference)
        member = place.projections[-1]
        assert isinstance(member, MIRField)
        return objects[reference.identity], member.id

    def read(place: MIRPlace) -> Value:
        value = values[place.root]
        for projection in place.projections:
            if isinstance(projection, MIRTupleIndex):
                assert isinstance(value, TupleValue)
                value = value.elements[projection.index]
            elif isinstance(projection, MIRDeref):
                assert isinstance(value, Reference)
            else:
                assert isinstance(projection, MIRField) and isinstance(value, Reference)
                value = objects[value.identity][projection.id]
        return value

    for _ in range(100):
        block = blocks[bid]
        for stmt in block.statements:
            rhs = stmt.value
            if isinstance(rhs, MIRConstant):
                value = rhs.value
            elif isinstance(rhs, MIRRead):
                value = read(rhs.source)
            elif isinstance(rhs, (MIRAlias, MIRBorrow)):
                value = values[rhs.source]
                assert isinstance(value, Reference)
            elif isinstance(rhs, MIRTupleConstruct):
                value = TupleValue(tuple(values[src] for src in rhs.elements))
            elif isinstance(rhs, MIRTupleCopy):
                source = values[rhs.source]
                assert isinstance(source, TupleValue)
                value = TupleValue(source.elements)
            elif isinstance(rhs, MIRConstruct):
                layout = records[slots[stmt.target.root].type]
                value = {f.id: values[src] for f, src in zip(layout.fields, rhs.fields)}
            elif isinstance(rhs, (MIRCopy, MIRMove)):
                source = rhs.source.root if isinstance(rhs, MIRCopy) else rhs.source
                reference = values[source]
                assert isinstance(reference, Reference)
                value = objects[reference.identity].copy()
            elif isinstance(rhs, MIRCompare):
                value = comparisons[rhs.op](values[rhs.left], values[rhs.right])
            elif isinstance(rhs, MIRNot):
                value = not values[rhs.operand]
            else:
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
                objects[reference.identity] = value
            elif stmt.target.projections:
                obj, member = field(stmt.target)
                assert not isinstance(value, Reference)
                obj[member] = value
            else:
                values[stmt.target.root] = value
        term = block.terminator
        if isinstance(term, MIRReturn):
            return values[term.value] if term.value is not None else None
        if isinstance(term, MIRBranch):
            bid = term.then if values[term.condition] else term.otherwise
        elif isinstance(term, MIRGoto):
            bid = term.target
        else:
            raise AssertionError(term)
    raise AssertionError("unexpected nontermination")
