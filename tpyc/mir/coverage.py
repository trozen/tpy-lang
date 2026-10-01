"""Fail-closed checks shared by body and definition coverage."""

from ..thir import nodes as th
from ..thir.metadata import unsupported_metadata
# `view_compatible` is the one predicate between a view holder and what it
# borrows: a TypeDef family pairing, a leaf fact rather than a MIR rule.
from ..thir.scalar_leaves import (  # noqa: F401
    owned_leaf, primitive_leaf, primitive_owned_leaf, storage_leaf, view_compatible, view_leaf,
)
from ..typesys import (
    FLOAT, INT32, FloatLiteralType, IntLiteralType, Representation, TpyType, passing_representation,
    through_view,
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
    """The representation a slot's value has: a parameter's passing
    convention (unknown when unpublished), else its form (a value and owned
    storage are their own storage, a holder points elsewhere)."""
    if slot.kind is MIRSlotKind.PARAMETER:
        return Representation.TRAIT if slot.passing is None else passing_representation(slot.passing)
    return (Representation.STORAGE if slot.value_kind in (MIRValueKind.SCALAR, MIRValueKind.OWNED)
            else Representation.REFERENCE)


def owned_storage(slot: MIRSlot) -> bool:
    """An OWNED slot of an owned leaf: storage the body holds by value."""
    return slot.value_kind is MIRValueKind.OWNED and owned_leaf(slot.type)


def owned_borrow(slot: MIRSlot) -> bool:
    """A readonly BORROWED holder of an owned leaf's storage."""
    return slot.value_kind is MIRValueKind.BORROWED and slot.readonly and owned_leaf(slot.type)


def view_holder(slot: MIRSlot) -> bool:
    """A readonly BORROWED holder typed by a view over an owned leaf
    (`view_leaf`): its referents are the owned-leaf storage it views."""
    return (slot.value_kind is MIRValueKind.BORROWED and slot.readonly and slot.form is th.Form.BORROW
            and view_leaf(slot.type))


def leaf_borrow(slot: MIRSlot) -> bool:
    """A holder an operation reads an owned leaf through: an owned leaf's
    own borrow or a view of one."""
    return owned_borrow(slot) or view_holder(slot)


def read_leaf(slot: MIRSlot) -> TpyType:
    """The leaf type an operation reading `slot` sees: a view holder reads
    its family's owned leaf."""
    return through_view(slot.type)


def primitive_operand(slot: MIRSlot) -> bool:
    """An operand a certified primitive operation reads: an inert leaf by
    value, or an owned leaf through a borrowed holder or a view, whose
    TypeDef carries the primitive-operation contract."""
    if leaf_borrow(slot):
        return primitive_owned_leaf(read_leaf(slot))
    return scalar_slot(slot) and primitive_leaf(slot.type)


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
            and any(isinstance(m, MIRTupleElement) and m.kind is MIRValueKind.OWNED
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
