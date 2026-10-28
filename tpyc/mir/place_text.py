"""How MIR spells a place, a summary path step and a source location: the
dumps' `%N` text, and the one storage naming (`MIRStorageNames`) behind its
two source renderers -- the annotation spelling the test harness matches
(`AnnotationSpelling`) and the Python words a warning uses (`spoken_place`)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from ..parse import SourceLocation
from ..thir import nodes as th
from ..thir.scalar_leaves import value_class
from ..typesys import TpyType, unwrap_readonly
from .call_contract import MIRAliasObligation
from .nodes import (
    MIRAlias, MIRAssign, MIRBorrow, MIRFunction, MIRIteratorAdvance, MIRIteratorInit, MIRPlace, MIRSlot, MIRSlotId,
    MIRSlotKind,
    source_order,
)
from .shapes import (
    MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload, optional_payload,
    sole_payload,
)
from .value import MIRField, MIRFieldId, MIRHolding


def _location(loc: SourceLocation | None) -> str:
    return f" @ {loc.line}:{loc.column}" if loc is not None else ""


def obligation_text(obligation: MIRAliasObligation) -> str:
    """`param2 vs param0[structure]`: the place the callee still uses, then
    the one it writes."""
    write = obligation.written
    return (f"param{obligation.parameter}" + "".join(map(_write_step, obligation.path))
            + f" vs param{write.parameter}" + "".join(map(_write_step, write.path)))


def _write_step(step: object) -> str:
    match step:
        case MIRContainerStructure():
            return "[structure]"
        case MIRContainerElements():
            return "[elements]"
        case MIRTupleIndex(index=index):
            return f"[{index}]"
        case MIRUnionPayload(alternative=alternative):
            return f".alternative[{alternative}]"
    return f".{step.name}"


def _place(place: MIRPlace) -> str:
    text = f"%{place.root.index}"
    for projection in place.projections:
        match projection:
            case MIRDeref():
                text = f"(*{text})"
            case MIRField(id=field):
                text += f".{field.owner.qualified_name()}::{field.name}"
            case MIRTupleIndex() | MIRUnionPayload() | MIRContainerStructure() | MIRContainerElements():
                text += _write_step(projection)
            case _:
                raise ValueError("unknown place projection")
    return text


# --- the storage naming -----------------------------------------------------------

_SELECTORS = {MIRSlotKind.PARAMETER: "param", MIRSlotKind.LOCAL: "local", MIRSlotKind.GLOBAL: "global"}


def source_name(slot: MIRSlot) -> str | None:
    """A slot's name in the source: a global's own name, a parameter's or a
    local's; none for a temporary or a compiler-made `__` name."""
    name = slot.global_id.name if slot.kind is MIRSlotKind.GLOBAL and slot.global_id is not None else slot.name
    if not name or name.startswith("__") or slot.kind is MIRSlotKind.TEMPORARY:
        return None
    return name


class MIRStorageNames:
    """The one naming of one body's storage. A named slot is named by its
    source name; storage with no name of its own (a constructed temporary)
    takes the name of the first named slot, in source order, that borrows it
    whole -- the binding that introduced it -- or the payload of an owned sum
    (the record a fill moved in).

    The annotation maps (`holders`, `storage`) spell a name whose slots sit
    under two selectors `param(s)` / `local(s)`, and a binding name that
    binds several storages `name@<line>`; the Python map (`spoken`) uses the
    bare source names only."""

    def __init__(self, fn: MIRFunction) -> None:
        self.function = fn
        self.slots = {slot.id: slot for slot in fn.slots}
        by_name: dict[str, dict[str, list[MIRSlotId]]] = {}
        for slot in fn.slots:
            selector = _SELECTORS.get(slot.kind)
            if selector is not None and slot.name is not None:
                by_name.setdefault(slot.name, {}).setdefault(selector, []).append(slot.id)
        self.names = {name: {selector: name if len(selectors) == 1 else f"{selector}({name})"
                             for selector in selectors} for name, selectors in by_name.items()}
        self.named = {sid: self.names[name][selector]
                      for name, selectors in by_name.items() for selector, ids in selectors.items() for sid in ids}
        candidates: dict[MIRPlace, list[tuple[tuple[int, int, int], str, int | None, int]]] = {}
        # A single definition of a temporary holder, which it is spelled through.
        self.definitions: dict[MIRSlotId, object] = {}
        defined: dict[MIRSlotId, int] = {}
        for block in fn.blocks:
            for stmt in block.statements:
                if not isinstance(stmt, MIRAssign):
                    continue
                # A cursor's advance keeps the container it walks.
                if not stmt.target.projections and not isinstance(stmt.value, MIRIteratorAdvance):
                    defined[stmt.target.root] = defined.get(stmt.target.root, 0) + 1
                    self.definitions[stmt.target.root] = stmt.value
                if (isinstance(stmt.value, MIRBorrow) and not stmt.target.projections
                        and stmt.target.root in self.named and (source := stmt.value.source).root not in self.named
                        and (not source.projections
                             or sole_payload(source.projections) and self.slots[source.root].shape.owned_sum)):
                    candidates.setdefault(source, []).append(
                        (source_order(stmt.loc), self.named[stmt.target.root], stmt.loc.line if stmt.loc else None,
                         stmt.target.root.index))
        for sid, count in defined.items():
            if count != 1:
                del self.definitions[sid]
        bindings = {place: min(found)[1:] for place, found in candidates.items()}
        bound = [name for name, _line, _sid in bindings.values()]
        self.holders: dict[MIRPlace, str] = {MIRPlace(sid): name for sid, name in self.named.items()}
        self.storage: dict[MIRPlace, str] = dict(self.holders)
        self.spoken: dict[MIRPlace, str] = {}
        for slot in fn.slots:
            if (name := source_name(slot)) is not None:
                self.spoken[MIRPlace(slot.id)] = name
        # Unnamed storage a binding introduced, apart from a slot's own name.
        self.bound: set[MIRPlace] = set()
        by_index = {slot.id.index: slot for slot in fn.slots}
        for place, (name, line, index) in bindings.items():
            # A name rebound to fresh storage names several objects; the binding line tells them apart.
            self.storage[place] = name if bound.count(name) == 1 else f"{name}@{line}"
            if (spoken := source_name(by_index[index])) is not None:
                self.spoken[place] = spoken
                self.bound.add(place)


def _reached_types(fn: MIRFunction) -> set[TpyType]:
    """The types a body's places can hold a record of: its slots' values
    and their members, and its layouts' records and fields."""
    types: set[TpyType] = set()
    stack = [slot.shape for slot in fn.slots]
    while stack:
        shape = stack.pop()
        if shape is not None:
            types.add(unwrap_readonly(shape.type))
            stack.extend(shape.members)
    for layout in fn.records:
        types.add(layout.type)
        types.update(unwrap_readonly(f.type) for f in layout.fields)
    return types


class AnnotationSpelling:
    """Spells places of one body as the `mir_*` annotations name them. A
    field is spelled by its name unless the record holding it declares that
    name under two owners; then by `Owner.name`, and the bare spelling is
    recorded as ambiguous with every owner's spelling."""

    def __init__(self, fn: MIRFunction) -> None:
        self.slots = {slot.id: slot for slot in fn.slots}
        # Per record the body reaches: each field name two owners declare, to its owners.
        self.shadowed: dict[TpyType, dict[str, tuple[str, ...]]] = {}
        for typ in _reached_types(fn):
            owners: dict[str, list[str]] = {}
            for f in fn.record_fields.get(typ, ()):
                owners.setdefault(f.id.name, []).append(f.id.owner.name)
            shadowed = {n: tuple(sorted(o)) for n, o in owners.items() if len(o) > 1}
            if shadowed:
                self.shadowed[typ] = shadowed
        self.ambiguous: dict[str, set[str]] = {}

    def owners(self, holder: TpyType | None, field: MIRFieldId) -> tuple[str, ...]:
        if holder is not None:
            return self.shadowed.get(holder, {}).get(field.name, ())
        # A record reached through a wrapper member: any record declaring the field decides.
        return next((names[field.name] for names in self.shadowed.values()
                     if field.owner.name in names.get(field.name, ())), ())

    def __call__(self, place: MIRPlace, names: Mapping[MIRPlace, str]) -> str | None:
        """`place` spelled from the name `names` gives the storage it lies
        in: its root slot's, or an owned sum payload's (`r` for the record a
        rebind of `r` filled)."""
        slot = self.slots.get(place.root)
        holder: TpyType | None = slot.type if slot is not None else None
        projections = place.projections
        root = names.get(MIRPlace(place.root))
        if sole_payload(place.projections[:1]) and (name := names.get(MIRPlace(place.root, projections[:1]))):
            root, projections = name, projections[1:]
            holder = slot.shape.members[place.projections[0].alternative].type
        if root is None:
            return None
        text = bare = root
        alternatives = [root]
        for projection in projections:
            match projection:
                case MIRField(id=field):
                    owners = self.owners(holder, field)
                    text += f".{field.owner.name}.{field.name}" if owners else f".{field.name}"
                    bare += f".{field.name}"
                    alternatives = [f"{a}.{o}.{field.name}" for a in alternatives for o in owners] if owners else [
                        f"{a}.{field.name}" for a in alternatives]
                    holder = unwrap_readonly(projection.type)
                    continue
                case MIRTupleIndex(index=index):
                    step = f"[{index}]"
                case MIRUnionPayload(alternative=alternative):
                    # Annotations spell an Optional's payload by its name.
                    held = None if holder is None else value_class(unwrap_readonly(holder))
                    step = "[payload]" if optional_payload(held, projection) else f"[alt{alternative}]"
                case MIRContainerStructure():
                    step = "[structure]"
                case MIRContainerElements():
                    step = "[elements]"
                case _:
                    continue  # a dereference stays on the record it points at
            text += step
            bare += step
            alternatives = [a + step for a in alternatives]
            holder = None
        if text != bare:
            self.ambiguous.setdefault(bare, set()).update(alternatives)
        return text


# --- the Python renderer ----------------------------------------------------------

def steps_spelling(steps: Iterable[object]) -> str:
    """Place or summary-path steps as Python spells them after their root,
    up to the first step it has no spelling for: a field `.name`, a tuple
    index `[i]`; a sum's payload has no token (`o.items`, `u.items`). A
    write in or under a container region is spelled by the container."""
    text = ""
    for step in steps:
        match step:
            case MIRField():
                text += f".{step.id.name}"
            case th.THIRFieldIdentity():
                text += f".{step.name}"
            case MIRTupleIndex():
                text += f"[{step.index}]"
            case MIRUnionPayload():
                pass
            case _:
                break
    return text


@dataclass(frozen=True)
class MIRSpokenPlace:
    """A place in the words a warning uses: the Python spelling of the
    storage path (`name`), where the storage it lies in comes from
    (`origin`: "named" -- a source name, "bound" -- unnamed storage named
    by the binding that introduced it, "temporary" -- an expression's
    storage spelled by its source text, "iteration" -- a loop's cursor
    over `name`), and the container region it lies in, when any
    ("elements", "structure"; whatever follows the region is not spelled)."""
    name: str
    origin: str
    region: str | None = None

    @property
    def quoted(self) -> str:
        return f"'{self.name}'"


Source = Callable[[SourceLocation], str | None]


def spoken_place(place: MIRPlace, names: MIRStorageNames, source: Source | None = None,
                 _depth: int = 0) -> MIRSpokenPlace | None:
    """`place` in Python words (`MIRSpokenPlace`), or None when nothing in
    the source names it: no `%N`, no region token, no `@line`, no owner
    qualification. A temporary holder defined once by a borrow or an alias
    is spelled by what it borrows; a loop cursor by the container it walks;
    other temporary storage by its expression's source text (`source`,
    over `MIRSlot.span`)."""
    slot = names.slots.get(place.root)
    if slot is None or _depth > 8:
        return None
    projections = place.projections
    root = names.spoken.get(MIRPlace(place.root))
    origin = "bound" if MIRPlace(place.root) in names.bound else "named"
    if root is None and sole_payload(projections[:1]) and (name := names.spoken.get(MIRPlace(place.root,
                                                                                            projections[:1]))):
        root, projections, origin = name, projections[1:], "bound"
    if root is None and slot.kind is MIRSlotKind.TEMPORARY:
        value = names.definitions.get(place.root)
        through: MIRPlace | None = None
        if isinstance(value, MIRBorrow):
            through = value.source
        elif isinstance(value, (MIRAlias, MIRIteratorInit)):
            through = MIRPlace(value.source)
        if through is not None:
            inner = spoken_place(through, names, source, _depth + 1)
            if inner is None:
                return None
            if slot.shape.holding is MIRHolding.CURSOR:
                return MIRSpokenPlace(inner.name, "iteration")
            if inner.region is not None or not projections:
                return inner if not projections else MIRSpokenPlace(inner.name, inner.origin, inner.region)
            return _with_steps(inner.name, inner.origin, projections)
        if slot.span is not None and source is not None and (text := source(slot.span)):
            root, origin = text, "temporary"
    if root is None:
        return None
    return _with_steps(root, origin, projections)


def _with_steps(root: str, origin: str, projections: tuple[object, ...]) -> MIRSpokenPlace:
    text = root
    for projection in projections:
        match projection:
            case MIRContainerElements():
                return MIRSpokenPlace(text, origin, "elements")
            case MIRContainerStructure():
                return MIRSpokenPlace(text, origin, "structure")
            case MIRDeref():
                continue
        text += steps_spelling((projection,))
    return MIRSpokenPlace(text, origin)
