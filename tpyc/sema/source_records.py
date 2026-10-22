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

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..parse import TpyExpr
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


class LeafPermission(Enum):
    """What an owning sink does with one reference leaf of a source.

    PASS: the slot does not own the leaf; a borrow passes as a borrow.
    MOVE: an owned leaf moves in, or the leaf is fresh.
    WARNED: an implicit copy, and its copy warning says so.
    HEDGED: an implicit copy of an open `T` leaf, and its definition-time
        hedge (`defer_own_copy_verdict`) says so.
    EXPLICIT: copied under `copy()`."""
    PASS = "pass"
    MOVE = "move"
    WARNED = "warned"
    HEDGED = "hedged"
    EXPLICIT = "explicit"

    @property
    def copies(self) -> bool:
        """The sink copies the leaf implicitly (the copy its warning says)."""
        return self in (LeafPermission.WARNED, LeafPermission.HEDGED)


@dataclass(frozen=True)
class Leaf:
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
        element by element."""
    NONE = "none"
    MOVE = "move"
    COPY = "copy"
    MIXED = "mixed"


@dataclass(frozen=True)
class SourceRecord:
    """`leaves`: every reference leaf path of the source at its slot. A
    select's verdict sits on the select's record; `arms` holds each arm's
    own record (its own ownership; an arm never warns on its own).

    The ONE classification of the record: `copied` / `moved` per path, and
    `disposition` over the whole. Every reader of an owning sink's
    copy-or-move verdict asks it here."""
    leaves: Mapping[ElemPath, Leaf]
    arms: tuple['SourceRecord', ...] = field(default=())

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
        moved = self.moved
        if moved:
            return (Disposition.MOVE if len(moved) == len(self.leaves)
                    else Disposition.MIXED)
        return Disposition.COPY if self.copied else Disposition.NONE

    @property
    def moves_whole(self) -> bool:
        """The sink takes the source whole by move: some leaf moves and
        every other passes (MOVE, or MIXED over passed leaves)."""
        perms = {leaf.permission for leaf in self.leaves.values()}
        return (LeafPermission.MOVE in perms
                and perms <= {LeafPermission.MOVE, LeafPermission.PASS})

    def element_moves(self, index: int) -> bool:
        """Whether the sink moves every leaf under top-level element
        `index` (an element holding no leaf moves nothing of its own)."""
        return all(leaf.permission is LeafPermission.MOVE
                   for p, leaf in self.leaves.items() if p[:1] == (index,))

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
