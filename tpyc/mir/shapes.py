"""The values MIR models: how a position holds one, and what each
projection reaches from it.

`from_thir` builds a slot's shape from THIR's facts, `field_shape` a
record field's, `container_member` a native container's members; `step`
is the one stepping table every reader of a place walks (the validator
adds its write rules on top, `validate.place_info`)."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace

from ..thir import nodes as th
from ..thir.scalar_leaves import (
    MIRClass, declared_members, modeled_field, value_class,
)
from ..typesys import ReadonlyType, Representation, TpyType, unwrap_readonly
from .call_contract import MIRContainerElements, MIRContainerStructure
from .nodes import (
    MEMBERED_CLASSES, MIRDeref, MIRField, MIRFieldId, MIRHolding, MIROptionalPayload, MIRPlace, MIRRecordLayout,
    MIRShape, MIRSlot, MIRSlotId, MIRTupleIndex, MIRUnionPayload, MIRValidationError,
)

Records = Mapping[TpyType, MIRRecordLayout]
Projection = (MIRDeref | MIRField | MIRTupleIndex | MIROptionalPayload | MIRUnionPayload
              | MIRContainerStructure | MIRContainerElements)

_LEAVES = (MIRClass.LEAF, MIRClass.OWNED_LEAF)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def holding_representation(holding: MIRHolding) -> Representation:
    """The representation a value held this way has: stored here (or a
    payload alias naming such storage) it is its own storage, any other
    holder points elsewhere."""
    return Representation.STORAGE if holding in (MIRHolding.VALUE, MIRHolding.ALIAS) else Representation.REFERENCE


def with_access(members: tuple[MIRShape | None, ...], readonly: bool) -> tuple[MIRShape | None, ...]:
    """A holder's view of a container's members: a readonly holder reaches
    readonly members, as `step` reaches them through it."""
    return tuple(m if m is None else replace(m, readonly=m.readonly or readonly) for m in members)


def container_member(typ: TpyType, leaves_only: bool,
                     layout_of: Callable[[TpyType], MIRRecordLayout | str]) -> MIRShape | str:
    """One member of a native container of declared member type `typ`: an
    inert leaf, an owned leaf, or (in a type with no value member,
    `leaves_only` false) a record whose layout (`layout_of`, or its refusal)
    has leaf fields only; else the refusal. The member's access is its own
    wrapper's; a holder adds its own (`with_access`)."""
    readonly = isinstance(typ, ReadonlyType)
    bare = unwrap_readonly(typ)
    cls = value_class(bare)
    if cls is MIRClass.LEAF:
        return MIRShape(bare, MIRClass.LEAF)
    if cls is MIRClass.OWNED_LEAF:
        return MIRShape(bare, MIRClass.OWNED_LEAF, readonly=readonly)
    if leaves_only:
        return "unsupported native container element"
    layout = layout_of(bare)
    if isinstance(layout, str):
        return layout
    if layout.opaque or not all(not isinstance(f.type, ReadonlyType) and value_class(f.type) in _LEAVES
                                for f in layout.fields):
        return "unsupported native container element"
    return MIRShape(bare, MIRClass.RECORD, readonly=readonly)


def expected_container_members(typ: TpyType, readonly: bool,
                               records: Records) -> tuple[MIRShape, ...] | None:
    """The members a holder of container `typ` with this access carries,
    re-derived from the type and the body's record layouts (an owned-leaf
    member needs its opaque one); None when `typ` is no admitted container
    or view of one."""
    members = declared_members(typ)
    if members is None:
        return None
    element, value, _ = members
    found: list[MIRShape] = []
    for member, leaves_only in ((element, value is not None), (value, True)):
        if member is None:
            continue
        shape = container_member(member, leaves_only, lambda t: records.get(t, "missing record layout"))
        if isinstance(shape, str) or shape.cls is MIRClass.OWNED_LEAF and not (
                shape.type in records and records[shape.type].opaque):
            return None
        found.append(shape)
    return with_access(tuple(found), readonly)


def loan_field(typ: TpyType) -> bool:
    """Whether a record member of declared type `typ` is the loan the record
    holds -- a view member, read or rebound whole -- rather than a value the
    record stores inline. The one rule every reader of a field asks."""
    return value_class(typ) is MIRClass.VIEW


def field_shape(typ: TpyType, readonly: bool, records: Records) -> MIRShape | None:
    """The value a record field of declared type `typ` stores, reached
    with access `readonly`; None for a field MIR does not model. Every
    member is stored inline except a view member, the loan the record
    holds (`loan_field`); an Optional record field stores its payload
    record inline; a container field's members are those its type admits,
    none when it admits none (no element place is reachable then)."""
    if not modeled_field(typ):
        return None
    cls = value_class(typ)
    access = readonly or isinstance(typ, ReadonlyType)
    bare = unwrap_readonly(typ)
    if loan_field(typ):
        return MIRShape(bare, cls, MIRHolding.POINTER, access)
    match cls:
        case MIRClass.CONTAINER:
            members = expected_container_members(bare, access, records)
            return MIRShape(bare, cls, MIRHolding.VALUE, access, members or ())
        case MIRClass.OPTIONAL:
            return MIRShape(bare, cls, MIRHolding.VALUE, access,
                            (MIRShape(bare.inner, MIRClass.RECORD, MIRHolding.VALUE, isinstance(typ, ReadonlyType)),))
    return MIRShape(bare, cls, MIRHolding.VALUE, access)


def record_pointer(typ: TpyType, readonly: bool) -> MIRShape:
    """A pointer holder at a record of type `typ`, at access `readonly`."""
    return MIRShape(typ, MIRClass.RECORD, MIRHolding.POINTER, readonly)


def thir_member(member: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> MIRShape:
    """A wrapper member as THIR selected it: a borrowed record is a pointer
    at it, an owned record inline storage, any other member an inert leaf
    by value."""
    match member:
        case th.THIRBorrowedRecord():
            return record_pointer(member.type, member.readonly)
        case th.THIROwnedRecord():
            return MIRShape(member.type, MIRClass.RECORD, MIRHolding.VALUE, member.readonly)
    return MIRShape(member, MIRClass.LEAF)


def from_thir(typ: TpyType, readonly: bool, *, storage: bool = False,
              reference: th.THIRBorrowedRecord | None = None,
              tuple_layout: th.THIRTupleLayout | None = None,
              optional_layout: th.THIROptionalLayout | None = None,
              union_layout: th.THIRUnionLayout | None = None, alias: bool = False,
              members: tuple[MIRShape, ...] = (), borrowed_container: bool = False,
              iterator: bool = False) -> MIRShape:
    """A slot's shape from the THIR facts its value is lowered from: a
    native iterator over a container, a borrowed container, owned storage,
    a borrow of a record, an owned leaf or a view (a container view's
    holder reaches its region directly), a tuple, a union, a payload alias,
    an Optional, else an inert leaf. `members` are a container holder's,
    at its access."""
    cls = value_class(typ)
    if iterator:
        holding = MIRHolding.CURSOR
    elif borrowed_container:
        holding = MIRHolding.REGION
    elif storage:
        holding = MIRHolding.VALUE
    elif reference is not None:
        holding = MIRHolding.REGION if cls is MIRClass.CONTAINER_VIEW else MIRHolding.POINTER
    elif tuple_layout is not None:
        return MIRShape(typ, MIRClass.TUPLE, readonly=readonly, members=tuple(map(thir_member, tuple_layout.elements)))
    elif union_layout is not None:
        return MIRShape(typ, MIRClass.UNION, readonly=readonly, members=tuple(
            None if m is None else thir_member(m) for m in union_layout.elements))
    elif alias:
        return MIRShape(typ, MIRClass.LEAF, MIRHolding.ALIAS, readonly)
    elif optional_layout is not None:
        return MIRShape(typ, MIRClass.OPTIONAL, readonly=readonly, members=(thir_member(optional_layout.payload),))
    else:
        return MIRShape(typ, MIRClass.LEAF, readonly=readonly)
    _require(cls is not None, "unsupported slot type")
    return MIRShape(typ, cls, holding, readonly, members)


def class_problem(shape: MIRShape, representation: Representation) -> str | None:
    """Whether a shape, and each member, is well formed: its type bare (its
    access is its `readonly` flag, never a wrapper), its class `value_class`
    of that type at the representation its holding gives it
    (`representation` for the shape itself), and members only on a wrapper
    or a container."""
    if isinstance(shape.type, ReadonlyType):
        return "readonly-wrapped value type"
    if shape.cls is not value_class(shape.type, representation):
        return "value class mismatch"
    if shape.members and shape.cls not in MEMBERED_CLASSES:
        return "members on a memberless value"
    for member in shape.members:
        if member is not None and (problem := class_problem(member, holding_representation(member.holding))):
            return problem
    return None


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
            _require(shape.region and bool(shape.members), "element projection needs container storage")
            member = shape.subscript
            return replace(member, readonly=shape.readonly or member.readonly)
        case MIRUnionPayload():
            _require(shape.cls is MIRClass.UNION, "union projection needs union payload")
            _require(type(projection.alternative) is int
                     and 0 <= projection.alternative < len(shape.members), "invalid union alternative")
            member = shape.alternatives[projection.alternative]
            _require(member is not None, "absent union alternative has no payload")
            return member
        case MIROptionalPayload():
            _require(shape.cls is MIRClass.OPTIONAL and bool(shape.members),
                     "optional projection needs optional payload")
            payload = shape.payload
            # An inline payload is only as writable as the path to it.
            inline = payload.cls is MIRClass.RECORD and payload.holding is MIRHolding.VALUE
            return replace(payload, readonly=shape.readonly or payload.readonly) if inline else payload
        case MIRTupleIndex():
            _require(shape.cls is MIRClass.TUPLE, "tuple projection needs tuple payload")
            _require(type(projection.index) is int
                     and 0 <= projection.index < len(shape.members), "tuple index out of range")
            return shape.elements[projection.index]
        case MIRDeref():
            # A container view's holder is its region: no dereference reaches past it.
            _require(shape.is_pointer, "dereference needs reference holder")
            return replace(shape, holding=MIRHolding.VALUE)
        case MIRField():
            typ = shape.type
            _require(shape.owned_place, "field needs record storage")
            _require(shape.cls is not MIRClass.VIEW, "field projection through a view")
            # An owned leaf's interior is opaque: nothing projects inside its buffer.
            _require(shape.cls is not MIRClass.OWNED_LEAF and not (typ in records and records[typ].opaque),
                     "field projection under an opaque layout")
            # A field is keyed by its declaring owner: the storage's type, or
            # an ancestor whose field its layout carries.
            _require(isinstance(projection.id, MIRFieldId) and bool(projection.id.name) and (
                projection.id.owner == typ
                or typ in records and MIRField(projection.id, projection.type) in records[typ].fields),
                "field owner mismatch")
            member = field_shape(projection.type, shape.readonly, records)
            _require(member is not None, "unsupported field type")
            return member
    raise MIRValidationError("unsupported place projections")


def _enter(previous: Projection | None, holder: MIRShape) -> None:
    # A view member is the loan a record stores, read or rebound whole.
    _require(not (isinstance(previous, MIRField) and holder.cls is MIRClass.VIEW),
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
