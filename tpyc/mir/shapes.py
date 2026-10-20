"""What each projection reaches from a value MIR models (`value`):
`step` is the one stepping table every reader of a place walks (the
validator adds its write rules on top, `validate.place_info`)."""

from collections.abc import Iterator, Mapping
from dataclasses import replace

from ..thir.scalar_leaves import MIRClass
from .call_contract import MIRContainerElements, MIRContainerStructure
from .nodes import MIRDeref, MIROptionalPayload, MIRPlace, MIRSlot, MIRSlotId, MIRTupleIndex, MIRUnionPayload
from .value import (
    MIRField, MIRFieldId, MIRHolding, MIRShape, MIRValidationError, Records, holder_form, loan_field, member_shape,
    require_valid,
)

Projection = (MIRDeref | MIRField | MIRTupleIndex | MIROptionalPayload | MIRUnionPayload
              | MIRContainerStructure | MIRContainerElements)


def step(shape: MIRShape, projection: Projection, records: Records) -> MIRShape:
    """The value `projection` reaches from a value of `shape`; raises on a
    projection the value has no member for. A container's structure is an
    analysis region with no value of its own: no step reaches it."""
    match projection:
        case MIRContainerStructure():
            raise MIRValidationError("summary regions are analysis places, not direct element accesses")
        case MIRContainerElements():
            # Any element: a read, borrow or in-place write of one reaches
            # the whole region of owned or borrowed container storage.
            require_valid(shape.region and bool(shape.members), "element projection needs container storage")
            member = shape.subscript
            return replace(member, readonly=shape.readonly or member.readonly)
        case MIRUnionPayload():
            require_valid(shape.cls is MIRClass.UNION, "union projection needs union payload")
            require_valid(type(projection.alternative) is int
                     and 0 <= projection.alternative < len(shape.members), "invalid union alternative")
            member = shape.alternatives[projection.alternative]
            require_valid(member is not None, "absent union alternative has no payload")
            return member
        case MIROptionalPayload():
            require_valid(shape.cls is MIRClass.OPTIONAL and bool(shape.members),
                     "optional projection needs optional payload")
            payload = shape.payload
            # An inline payload is only as writable as the path to it.
            inline = payload.cls is MIRClass.RECORD and payload.holding is MIRHolding.VALUE
            return replace(payload, readonly=shape.readonly or payload.readonly) if inline else payload
        case MIRTupleIndex():
            require_valid(shape.cls is MIRClass.TUPLE, "tuple projection needs tuple payload")
            require_valid(type(projection.index) is int
                     and 0 <= projection.index < len(shape.members), "tuple index out of range")
            return shape.elements[projection.index]
        case MIRDeref():
            # A container view's holder is its region: no dereference reaches past it.
            require_valid(shape.is_pointer, "dereference needs reference holder")
            return replace(shape, holding=MIRHolding.VALUE)
        case MIRField():
            typ = shape.type
            require_valid(shape.owned_place, "field needs record storage")
            require_valid(shape.cls is not MIRClass.VIEW, "field projection through a view")
            # An owned leaf's interior is opaque: nothing projects inside its buffer.
            require_valid(shape.cls is not MIRClass.OWNED_LEAF and not (typ in records and records[typ].opaque),
                     "field projection under an opaque layout")
            # A field is keyed by its declaring owner: the storage's type, or
            # an ancestor whose field its layout carries.
            require_valid(isinstance(projection.id, MIRFieldId) and bool(projection.id.name) and (
                projection.id.owner == typ
                or typ in records and MIRField(projection.id, projection.type) in records[typ].fields),
                "field owner mismatch")
            return member_shape(projection.type, shape.readonly, records)
    raise MIRValidationError("unsupported place projections")


def inline_payload_holder(field: MIRShape, records: Records) -> MIRShape:
    """A holder of an Optional field's inline payload record, at the
    access of the place the payload lies in (`step`)."""
    return holder_form(step(field, MIROptionalPayload(), records))


def _enter(previous: Projection | None, holder: MIRShape) -> None:
    # A view member is the loan a record stores, read or rebound whole.
    require_valid(not (isinstance(previous, MIRField) and holder.cls is MIRClass.VIEW),
             "projection through a view member")


def walk(shape: MIRShape, projections: tuple[Projection, ...],
         records: Records) -> Iterator[tuple[Projection, MIRShape, MIRShape]]:
    """Each projection with the shape it applies to and the shape it
    reaches, from a value of `shape`."""
    previous: Projection | None = None
    for projection in projections:
        _enter(previous, shape)
        reached = step(shape, projection, records)
        yield projection, shape, reached
        previous, shape = projection, reached


def walk_to(shape: MIRShape, projections: tuple[Projection, ...], records: Records) -> MIRShape:
    """The value `projections` reach from a value of `shape`."""
    for _, _, shape in walk(shape, projections, records):
        pass
    return shape


def prefix_shapes(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
                  records: Records) -> Iterator[tuple[Projection, MIRShape]]:
    """Each projection of `place` with the shape of the prefix it applies
    to. Lazy: a projection is stepped only once the walk moves past it, so
    a walk may stop at a projection no step reaches (a structure region)."""
    shape = slots[place.root].shape
    previous: Projection | None = None
    for projection in place.projections:
        if previous is not None:
            shape = step(shape, previous, records)
        _enter(previous, shape)
        yield projection, shape
        previous = projection


def place_shape(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: Records) -> MIRShape:
    """The value `place` denotes."""
    return walk_to(slots[place.root].shape, place.projections, records)


def view_member(place: MIRPlace) -> MIRField | None:
    """The view member a place ends at -- the loan a record object stores,
    read or rebound whole -- or None."""
    last = place.projections[-1] if place.projections else None
    return last if isinstance(last, MIRField) and loan_field(last.type) else None
