"""A bounded MIR interpreter with explicit shared object identities."""

from dataclasses import dataclass
import operator

from .nodes import (
    MIRAlias, MIRBranch, MIRCompare, MIRConstant, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRSlotKind,
)


@dataclass(frozen=True)
class Reference:
    identity: int


Value = int | bool | Reference
Heap = dict[int, dict[MIRFieldId, int | bool]]


def execute(fn: MIRFunction, *args: Value, heap: Heap | None = None) -> Value | None:
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert len(params) == len(args)
    values = dict(zip(params, args))
    objects = heap if heap is not None else {}
    blocks = {b.id: b for b in fn.blocks}
    bid = fn.entry
    comparisons = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}

    def field(place: MIRPlace) -> tuple[dict[MIRFieldId, int | bool], MIRFieldId]:
        reference = values[place.root]
        assert isinstance(reference, Reference)
        member = place.projections[1]
        assert isinstance(member, MIRField)
        return objects[reference.identity], member.id

    def read(place: MIRPlace) -> Value:
        if not place.projections:
            return values[place.root]
        obj, member = field(place)
        return obj[member]

    for _ in range(100):
        block = blocks[bid]
        for stmt in block.statements:
            rhs = stmt.value
            if isinstance(rhs, MIRConstant):
                value = rhs.value
            elif isinstance(rhs, MIRRead):
                value = read(rhs.source)
            elif isinstance(rhs, MIRAlias):
                value = values[rhs.source]
                assert isinstance(value, Reference)
            elif isinstance(rhs, MIRCompare):
                value = comparisons[rhs.op](values[rhs.left], values[rhs.right])
            elif isinstance(rhs, MIRNot):
                value = not values[rhs.operand]
            else:
                raise AssertionError(rhs)
            if stmt.target.projections:
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
