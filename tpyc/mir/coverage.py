"""Fail-closed checks shared by body and definition coverage."""

from collections.abc import Mapping

from ..thir import nodes as th
from ..thir.metadata import unsupported_metadata
# `view_compatible` is the one predicate between a view holder and what it
# borrows: a TypeDef family pairing, a leaf fact rather than a MIR rule.
from ..thir.scalar_leaves import (  # noqa: F401
    MIRClass, primitive_leaf, primitive_owned_leaf, storage_leaf, view_compatible,
)
from ..typesys import (
    FLOAT, INT32, FloatLiteralType, IntLiteralType, Representation, TpyType, passing_representation, through_view,
)
from .call_contract import MIRContainerElements, MIRContainerStructure, MIRParameterBinding
from .nodes import (
    MIRValidationError, MIRCall, MIRDeref, MIRField, MIRFieldId, MIROptionalPayload, MIRPlace,
    MIRSlot, MIRSlotId, MIRSlotKind,
)
from .shapes import holding_representation


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


def scalar_param(binding: MIRParameterBinding) -> bool:
    """A parameter bound as the callee's own scalar value
    (`MIRParameterBinding.scalar_value`)."""
    return binding.scalar_value


def slot_representation(slot: MIRSlot) -> Representation:
    """The representation a slot's value has: a parameter's passing
    convention (unknown when unpublished), else its holding's (a value and
    owned storage are their own storage, a holder points elsewhere)."""
    if slot.kind is MIRSlotKind.PARAMETER:
        return Representation.TRAIT if slot.passing is None else passing_representation(slot.passing)
    return holding_representation(slot.shape.holding)


def path_step(item: object) -> MIRField | MIRContainerStructure | MIRContainerElements:
    """One item of a summary's parameter path as a MIR projection."""
    match item:
        case th.THIRFieldIdentity(owner=owner, name=name, type=typ):
            return MIRField(MIRFieldId(owner, name), typ)
        case MIRContainerStructure() | MIRContainerElements():
            return item
    raise MIRValidationError("unknown call path item")


def whole_wrapper_field(place: MIRPlace) -> MIRPlace:
    """A place under an Optional record field's payload, published as the
    whole field: summary paths have no payload selector, and replacing the
    field reaches everything a payload write does."""
    for index, projection in enumerate(place.projections):
        if isinstance(projection, MIROptionalPayload):
            return MIRPlace(place.root, place.projections[:index])
    return place


def summary_path(place: MIRPlace) -> tuple[object, ...]:
    """The projections of a place under a parameter, spelled as a summary
    path (`path_step` reads it back): each field by its THIR identity, any
    other step as it is (the path grammar refuses what it does not admit)."""
    return tuple(th.THIRFieldIdentity(p.id.owner, p.id.name, p.type) if isinstance(p, MIRField) else p
                 for p in place.projections)


def call_place(call: MIRCall, parameter: int, path: tuple[object, ...],
               slots: Mapping[MIRSlotId, MIRSlot]) -> MIRPlace:
    """The caller's place a summary's parameter path names: under the
    record a borrowed record argument points at, or directly under a
    container argument (owned, borrowed, or a view)."""
    argument = slots[call.arguments[parameter]]
    through = argument.shape.is_pointer
    if argument.shape.cls is MIRClass.OPTIONAL:
        # An Optional argument lends the record its payload points at; a
        # scalar payload is the callee's own copy, under which no path lies.
        if not argument.shape.record_payload:
            raise MIRValidationError("call path under an Optional argument with no borrowed payload")
        return MIRPlace(argument.id, PAYLOAD_RECORD + tuple(map(path_step, path)))
    return MIRPlace(argument.id, ((MIRDeref(),) if through else ()) + tuple(map(path_step, path)))


def _path_key(path: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(item.name if isinstance(item, th.THIRFieldIdentity) else type(item).__name__ for item in path)


def call_write_places(call: MIRCall, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[MIRPlace, ...]:
    """The caller's places a call may write: each summarized write's
    parameter path (`call_place`)."""
    return tuple(call_place(call, write.parameter, write.path, slots)
                 for write in sorted(call.present_effects.writes, key=lambda w: (w.parameter, _path_key(w.path))))


def read_leaf(slot: MIRSlot) -> TpyType:
    """The leaf type an operation reading `slot` sees: a view holder reads
    its family's owned leaf."""
    return through_view(slot.type)


def primitive_operand(slot: MIRSlot) -> bool:
    """An operand a certified primitive operation reads: an inert leaf by
    value, or an owned leaf through a borrowed holder or a view, whose
    TypeDef carries the primitive-operation contract."""
    if slot.shape.leaf_borrow:
        return primitive_owned_leaf(read_leaf(slot))
    return slot.shape.is_scalar and primitive_leaf(slot.type)


# The steps from an Optional record holder to the record its payload points at.
PAYLOAD_RECORD = (MIROptionalPayload(), MIRDeref())


def payload_record_path(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[object, ...] | None:
    """The projections under the record an Optional record holder's payload
    points at (`payload, deref, ...`), or None for any other place."""
    if slots[place.root].shape.record_payload and place.projections[:2] == PAYLOAD_RECORD:
        return place.projections[2:]
    return None


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
