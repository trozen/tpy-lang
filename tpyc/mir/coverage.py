"""Fail-closed checks shared by body and definition coverage."""

from ..thir import nodes as th
from ..thir.metadata import unsupported_metadata
from ..thir.scalar_leaves import storage_leaf
from ..typesys import (
    FLOAT, INT32, FloatLiteralType, IntLiteralType, Representation, TpyType, passing_representation,
)
from .nodes import MIROptionalLayout, MIRSlot, MIRSlotKind, MIRTupleElement, MIRTupleLayout, MIRValueKind


def literal_type(expr: th.THIRLiteral, expected: TpyType | None = None) -> TpyType:
    """The type of the leaf a scalar literal holds. A number literal sema
    left unresolved converts to its context's leaf type when the context
    names one; alone it is the C++ literal it spells (`int` / `double`).
    Whether the value fits is `scalar_leaves.leaf_constant`'s question."""
    typ = expr.result_type
    if isinstance(typ, (IntLiteralType, FloatLiteralType)):
        if expected is not None and storage_leaf(expected):
            return expected
        return INT32 if isinstance(typ, IntLiteralType) else FLOAT
    return typ


def scalar_param(p: th.THIRParam) -> bool:
    """A parameter holding an inert leaf at the passing THIR published for
    it; a parameter without a published passing is refused."""
    return p.passing is not None and storage_leaf(p.type, passing_representation(p.passing))


def slot_representation(slot: MIRSlot) -> Representation:
    """The representation a scalar slot's value has: a parameter's passing
    convention (unknown when unpublished), else its form (a value is its
    own storage)."""
    if slot.kind is MIRSlotKind.PARAMETER:
        return Representation.TRAIT if slot.passing is None else passing_representation(slot.passing)
    return Representation.STORAGE if slot.value_kind is MIRValueKind.SCALAR else Representation.REFERENCE


def scalar_slot(slot: MIRSlot) -> bool:
    """A SCALAR slot whose type is re-verified an inert leaf at its
    representation, so no loan can start, pass through or end at it."""
    return slot.value_kind is MIRValueKind.SCALAR and storage_leaf(slot.type, slot_representation(slot))


def scalar_member(member: MIRTupleElement | MIROptionalLayout | None) -> bool:
    """A wrapper or container member holding an inert leaf by value."""
    return (member is not None and member.kind is MIRValueKind.SCALAR
            and not member.readonly and storage_leaf(member.type))

def owned_tuple(slot: MIRSlot) -> bool:
    return (slot.value_kind is MIRValueKind.TUPLE and isinstance(slot.tuple_layout, MIRTupleLayout)
            and any(isinstance(m, MIRTupleElement) and m.kind is MIRValueKind.RECORD_STORAGE
                    for m in slot.tuple_layout.elements))


def scalar_wrapper(slot: MIRSlot) -> bool:
    match slot.value_kind:
        case MIRValueKind.OPTIONAL:
            return scalar_member(slot.optional_layout)
        case MIRValueKind.UNION:
            return all(m is None or scalar_member(m) for m in slot.union_layout.elements)
        case _:
            return False


class MIRUnsupported(Exception):
    def __init__(self, node: object, reason: str) -> None:
        self.node = node
        self.reason = reason


def require(node: object, condition: bool, reason: str) -> None:
    if not condition:
        raise MIRUnsupported(node, reason)


def plain(node: object, allowed: set[str]) -> None:
    # New non-default metadata must not silently acquire scalar semantics.
    member = unsupported_metadata(node, allowed)
    require(node, member is None, f"unsupported metadata: {member}")
