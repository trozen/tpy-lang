"""The projections a place takes and what each reaches from a value MIR
models (`value`): `step` is the one stepping table every reader of a
place walks (the validator adds its write rules on top,
`validate.place_info`). A leaf: of the sibling `tpyc.mir` modules it
imports only `value`."""

from dataclasses import dataclass, replace

from ..thir.scalar_leaves import MIRClass
from ..typesys import TpyType
from .value import (
    SUM_CLASSES, MIRField, MIRFieldId, MIRHolding, MIRRecords, MIRShape, MIRValidationError, RecordFields, holder_form,
    member_shape, require_valid,
)


@dataclass(frozen=True)
class MIRDeref:
    pass


@dataclass(frozen=True)
class MIRTupleIndex:
    index: int


@dataclass(frozen=True)
class MIRUnionPayload:
    """A sum's selected payload: a union's alternative, an Optional's 1."""
    alternative: int


@dataclass(frozen=True)
class MIRContainerStructure:
    """A native container's shape: its length, its buffer and the slots
    its iterators walk."""


@dataclass(frozen=True)
class MIRContainerElements:
    """Any element of a native container, as one region: element identity
    is never tracked, so a write of one element is a write of any."""


Projection = (MIRDeref | MIRField | MIRTupleIndex | MIRUnionPayload
              | MIRContainerStructure | MIRContainerElements)

# An Optional's payload: the sum's alternative beside the absent 0.
OPTIONAL_PAYLOAD = MIRUnionPayload(1)


def optional_payload(holder: MIRClass | None, projection: Projection) -> bool:
    """Whether `projection`, taken from a value of class `holder`, selects
    an Optional's payload."""
    return holder is MIRClass.OPTIONAL and projection == OPTIONAL_PAYLOAD


REGION_END = "summary regions are analysis places, not direct element accesses"


def field_member(fields: RecordFields, holder: TpyType, owner: TpyType, name: str, typ: TpyType) -> str | None:
    """Whether the field (`owner`, `name`, `typ`), named by a non-empty
    string, is a member of record storage of type `holder`: one of the
    holder's declared fields (`fields`), each keyed by its declaring
    owner -- the one answer every walk gets. Else the problem."""
    if not (isinstance(name, str) and name):
        return "field owner mismatch"
    return None if MIRField(MIRFieldId(owner, name), typ) in fields.get(holder, ()) else (
        "field missing from declared fields")


def step(shape: MIRShape, projection: Projection, records: MIRRecords) -> MIRShape:
    """The value `projection` reaches from a value of `shape`; raises on a
    projection the value has no member for. A container's structure is an
    analysis region with no value of its own: no step reaches it."""
    match projection:
        case MIRContainerStructure():
            raise MIRValidationError(REGION_END)
        case MIRContainerElements():
            # Any element: a read, borrow or in-place write of one reaches
            # the whole region of container storage (a borrowed container's
            # after its dereference) or of the container a view views.
            require_valid((shape.owned_place and shape.cls is MIRClass.CONTAINER or shape.container_view_holder)
                          and bool(shape.members),
                          "element projection needs container storage")
            return shape.subscript
        case MIRUnionPayload():
            require_valid(shape.cls in SUM_CLASSES and bool(shape.members), "payload projection needs a sum")
            require_valid(type(projection.alternative) is int
                     and 0 <= projection.alternative < len(shape.members), "invalid union alternative")
            member = shape.alternatives[projection.alternative]
            require_valid(member is not None, "absent alternative has no payload")
            return member
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
            # Only record storage has fields: a view, an owned leaf's buffer or a
            # container's storage (elements only) has none.
            require_valid(shape.owned_place and shape.cls is MIRClass.RECORD, "field needs record storage")
            require_valid(isinstance(projection.id, MIRFieldId), "field owner mismatch")
            problem = field_member(records.fields, typ, projection.id.owner, projection.id.name, projection.type)
            require_valid(problem is None, problem)
            return member_shape(projection.type, shape.readonly, records)
    raise MIRValidationError("unsupported place projections")


def inline_payload_holder(field: MIRShape, records: MIRRecords) -> MIRShape:
    """A holder of an Optional field's inline payload record, at the
    access of the place the payload lies in (`step`)."""
    return holder_form(step(field, OPTIONAL_PAYLOAD, records))


def _enter(previous: Projection | None, holder: MIRShape) -> None:
    # A view member is the loan a record stores, read or rebound whole.
    require_valid(not (isinstance(previous, MIRField) and holder.cls is MIRClass.VIEW),
             "projection through a view member")


@dataclass(frozen=True)
class MIRHop:
    """One projection of a walk: the shape it applies to and the shape it
    reaches -- None for a container's structure, the terminal region."""
    projection: Projection
    holder: MIRShape
    reached: MIRShape | None


@dataclass(frozen=True)
class MIRWalk:
    """A place's projections stepped from its root's shape (`walk`)."""
    root: MIRShape
    hops: tuple[MIRHop, ...]

    @property
    def end(self) -> MIRShape | None:
        """What the place denotes: the last reached shape, the root for an
        empty path; None for the terminal region (`[structure]`), whose
        hop's holder is the container -- only a write target."""
        return self.hops[-1].reached if self.hops else self.root


def walk(shape: MIRShape, projections: tuple[Projection, ...], records: MIRRecords) -> MIRWalk:
    """Each projection with the shape it applies to and the shape it
    reaches, from a value of `shape`; raises on a hop no rule admits. A
    container's structure is the terminal region: nothing follows it."""
    hops: list[MIRHop] = []
    holder: MIRShape | None = shape
    previous: Projection | None = None
    for projection in projections:
        require_valid(not isinstance(previous, MIRContainerStructure), REGION_END)
        _enter(previous, holder)
        reached = None if isinstance(projection, MIRContainerStructure) else step(holder, projection, records)
        hops.append(MIRHop(projection, holder, reached))
        previous, holder = projection, reached
    return MIRWalk(shape, tuple(hops))


def object_prefix(shape: MIRShape, *, payload: bool) -> tuple[Projection, ...]:
    """The projections from a value of `shape` to the object it holds: the
    dereference of a pointer; with `payload`, the record an Optional
    record's payload points at; nothing for storage or a value held in
    place."""
    if shape.is_pointer:
        return (MIRDeref(),)
    if payload and shape.record_payload:
        return (OPTIONAL_PAYLOAD, MIRDeref())
    return ()


def store_problem(walked: MIRWalk) -> str | None:
    """Whether a store may write the place a walk ends at, by TPy's write
    rule; None when it may, else the problem. Every holder on the way is
    mutable (a pointer's access is its object's); replacing a field also
    needs the field mutable (not declared `readonly[...]`), replacing an
    element only its container: a `list[readonly[R]]` element is replaced,
    never written into."""
    for hop in walked.hops:
        if hop.holder.readonly:
            match hop.projection:
                case MIRDeref():
                    return "store through readonly reference"
                case MIRContainerElements() | MIRContainerStructure():
                    return "store through readonly container"
            return "store through readonly storage"
    last = walked.hops[-1] if walked.hops else None
    if last is not None and isinstance(last.projection, MIRField) and last.reached.readonly:
        return "store through readonly storage"
    return None


def walk_to(shape: MIRShape, projections: tuple[Projection, ...], records: MIRRecords) -> MIRShape:
    """The value `projections` reach from a value of `shape`."""
    end = walk(shape, projections, records).end
    require_valid(end is not None, REGION_END)
    return end
