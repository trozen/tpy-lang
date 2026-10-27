"""The per-leaf ownership record of a SOURCE at an owning sink.

Every owning sink sema checks (a return, an `Own` or `Own`-element
argument, a field or container store, a member-init, a store into `Any`, a
container literal element, an owned-element yield, a consuming receiver, a
captured return) registers the source it takes and its slot (`SinkRead`)
when the slot holds a reference leaf; once the body's ownership fact is
settled, one builder (`TypeCompatibility.build_source_records`) gives each
source one `SourceRecord`: per reference leaf path -- a tuple's `(i,)`,
`(i, j)`, a scalar's one leaf at `()` -- whether the payload arrives
borrowed, whether the slot owns that leaf, and what the sink does with it.
The record is keyed by the canonical source node (`peel_value_wrappers`),
so a coercion or a walrus wrapped around the source reads the same record.

Whether a leaf COPIES comes from the source facts the builder reads, a
name read's from the one ownership verdict (`TypeCompatibility.name_moves`);
a copied leaf is WARNED, or HEDGED for an open payload, by its own type.
The copy diagnostics are projected from it: the walk only registers each
owning sink's copy diagnostic (`context.PendingCopy`), and settlement emits
it iff a leaf it speaks for copies (`TypeCompatibility.project_copies`).

Every reader of an owning sink's copy-or-move verdict -- sema's settlement
and the lowering alike -- asks the record one way: `read_record` /
`verdict` and the classification on `SourceRecord` (the per-path
`copied` / `moved` leaves and the `Disposition` over the whole).

No record means the source was never checked. A record with no leaf for a
value-bounded element (`T: ValueType`) means its copy is proven harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..parse import TpyExpr, TpyStmt
from ..typesys import TpyType
from ..value_category import peel_value_wrappers

if TYPE_CHECKING:
    from .alias_rebind import ElemPath
    from .compatibility import TupleSink
    from .context import SemanticContext


@dataclass(frozen=True)
class SinkRead:
    """One owning sink's registration: the `source` as written (a walrus
    is its target name's read), the `slot` it fills and the `sink` kind
    (None: an `Own` slot, a return's or an argument's)."""
    source: TpyExpr
    slot: TpyType
    sink: 'TupleSink | None' = None
    # The declaration, rebind or walrus binding the source to a name: the
    # leaves the slot owns are those that binding owns at its site
    # (`site_ownership`), as a local's declared type carries no `Own`
    # markers.
    binds: 'TpyStmt | TpyExpr | None' = None
    # Whether the sink's check declares the copies it makes (a warning,
    # a hedge); a sink no check speaks for records them UNDECLARED.
    declares_copies: bool = True


class LeafPermission(Enum):
    """What an owning sink does with one reference leaf of a source.

    PASS: the slot does not own the leaf; a borrow passes as a borrow.
    MOVE: an owned leaf moves in, or the leaf is fresh.
    WARNED: an implicit copy, and its copy warning says so.
    HEDGED: an implicit copy of an open `T` leaf, and its definition-time
        hedge (`defer_own_copy_verdict`) says so.
    EXPLICIT: copied under `copy()`.
    UNDECLARED: a copy the sink would make that no check declares: the
        lowering refuses it.
    VALUE: a copy a `ValueType` bound makes unobservable (`T`,
        `T | int32`): permitted with no diagnostic; not an implicit copy
        (`copies`), and the leaf still moves at a real last use."""
    PASS = "pass"
    MOVE = "move"
    WARNED = "warned"
    HEDGED = "hedged"
    EXPLICIT = "explicit"
    UNDECLARED = "undeclared"
    VALUE = "value"

    @property
    def copies(self) -> bool:
        """The sink copies the leaf implicitly (the copy its warning says)."""
        return self in (LeafPermission.WARNED, LeafPermission.HEDGED)


@dataclass(frozen=True)
class Leaf:
    # The leaf's value comes from existing storage (a name, an element or
    # a field it moves out of, a borrow it copies); False only for a new
    # value -- a fresh call result, a construction, a literal temporary.
    borrowed: bool
    slot_owns: bool
    permission: LeafPermission


class Disposition(Enum):
    """What an owning sink does with a source as a whole, from its leaves.

    NONE: no leaf moves and none copies implicitly (every leaf passes or
        is copied under `copy()`; or no sink took the source).
    MOVE: every leaf moves.
    COPY: some leaf copies implicitly and none moves -- the copy of the
        source whole, its passed leaves passed on.
    MIXED: some leaf moves and another does not -- the source converts
        element by element.

    An element's record (`SourceRecord.under`) is COPY exactly when the
    whole it is part of is; one whose own leaves copy inside a whole that
    converts element by element is MIXED."""
    NONE = "none"
    MOVE = "move"
    COPY = "copy"
    MIXED = "mixed"


@dataclass(frozen=True)
class SourceRecord:
    """`leaves`: every reference leaf path of the source at its slot. A
    select's verdict sits on the select's record.

    The ONE classification of the record: `copied` / `moved` per path, and
    `disposition` over the whole. Every reader of an owning sink's
    copy-or-move verdict asks it here.

    `part_of_copy`: None for a source's own record; for an element's
    (`under`), whether the sink copies the whole it is part of."""
    leaves: Mapping[ElemPath, Leaf]
    part_of_copy: bool | None = None

    @property
    def copied(self) -> frozenset[ElemPath]:
        """The leaf paths the sink copies implicitly (WARNED / HEDGED)."""
        return frozenset(p for p, leaf in self.leaves.items()
                         if leaf.permission.copies)

    @property
    def moved(self) -> frozenset[ElemPath]:
        """The leaf paths the sink moves."""
        return frozenset(p for p, leaf in self.leaves.items()
                         if leaf.permission is LeafPermission.MOVE)

    @property
    def disposition(self) -> Disposition:
        """A VALUE leaf decides nothing: its copy and its move are one
        (`LeafPermission.VALUE`). An element's is COPY exactly where the
        whole's is (`part_of_copy`)."""
        if self.part_of_copy:
            return Disposition.COPY
        moved = self.moved
        if moved:
            decided = [leaf for leaf in self.leaves.values()
                       if leaf.permission is not LeafPermission.VALUE]
            return (Disposition.MOVE if len(moved) == len(decided)
                    else Disposition.MIXED)
        if not self.copied:
            return Disposition.NONE
        return (Disposition.MIXED if self.part_of_copy is False
                else Disposition.COPY)

    @property
    def moves_whole(self) -> bool:
        """The sink takes the source whole by move: some leaf moves and
        every other passes or is a VALUE leaf (MOVE, or MIXED over passed
        leaves)."""
        perms = {leaf.permission for leaf in self.leaves.values()}
        return (LeafPermission.MOVE in perms
                and perms <= {LeafPermission.MOVE, LeafPermission.PASS,
                              LeafPermission.VALUE})

    def element_moves(self, index: int) -> bool:
        """Whether the sink moves every leaf under top-level element
        `index` (an element holding no leaf moves nothing of its own); a
        VALUE leaf moves with it."""
        return all(leaf.permission in (LeafPermission.MOVE,
                                       LeafPermission.VALUE)
                   for p, leaf in self.leaves.items() if p[:1] == (index,))

    @property
    def fresh(self) -> frozenset[ElemPath]:
        """The leaf paths the sink moves a new value into: no existing
        object, so a copy of one copies nothing the program holds."""
        return frozenset(p for p, leaf in self.leaves.items()
                         if leaf.permission is LeafPermission.MOVE
                         and not leaf.borrowed)

    @property
    def copies_whole(self) -> bool:
        """The sink copies the source whole (`Disposition.COPY`), or the
        whole this element record is part of."""
        return self.disposition is Disposition.COPY

    @property
    def copy_permitted(self) -> frozenset[ElemPath]:
        """The leaf paths a copy may take: those the sink copies (WARNED /
        HEDGED), copies under `copy()` (EXPLICIT) or copies unobservably
        (VALUE), and the root `()` -- every leaf -- where it copies the
        whole."""
        out = frozenset(p for p, leaf in self.leaves.items()
                        if leaf.permission.copies
                        or leaf.permission in (LeafPermission.EXPLICIT,
                                               LeafPermission.VALUE))
        return out | {()} if self.copies_whole else out

    def under(self, index: int) -> 'SourceRecord':
        """The record of top-level element `index`, relative to it: the
        leaves under it, and a root leaf `()` -- a `copy()` of the whole --
        kept visible to every element."""
        leaves = {p[1:]: leaf for p, leaf in self.leaves.items()
                  if p[:1] == (index,)}
        root = self.leaves.get(())
        if root is not None:
            leaves[()] = root
        return SourceRecord(leaves, part_of_copy=self.copies_whole)

    def copies_at(self, path: ElemPath) -> bool | None:
        """Whether the sink copies a leaf at or under `path`; None where
        the record holds no leaf there."""
        mine = [leaf for p, leaf in self.leaves.items()
                if p[:len(path)] == path]
        return any(leaf.permission.copies for leaf in mine) if mine else None


NO_RECORD = SourceRecord({})


def read_record(ctx: 'SemanticContext',
                expr: TpyExpr | None) -> SourceRecord | None:
    """The record an owning sink published for source `expr`, relative to
    it: its own record, or -- a member of a literal source -- the literal
    record's leaves under the member's path. Looked up by the canonical
    node (`peel_value_wrappers`: coercions and walruses peeled), as it was
    keyed. None when no sink checked it."""
    if expr is None:
        return None
    key = peel_value_wrappers(expr)
    rec = ctx.source_records.get(key)
    if rec is not None:
        return rec
    at = ctx.member_sources.get(key)
    if at is None:
        return None
    whole_key, prefix = at
    whole = ctx.source_records.get(whole_key)
    if whole is None:
        return None
    n = len(prefix)
    return SourceRecord({p[n:]: leaf for p, leaf in whole.leaves.items()
                         if p[:n] == prefix})


def verdict(ctx: 'SemanticContext', expr: TpyExpr | None) -> SourceRecord:
    """`read_record`, an empty record (NONE, nothing copied or moved) for a
    source no sink checked."""
    rec = read_record(ctx, expr)
    return NO_RECORD if rec is None else rec


def publish_source_record(ctx: 'SemanticContext', expr: TpyExpr,
                          record: SourceRecord) -> None:
    ctx.source_records[peel_value_wrappers(expr)] = record
