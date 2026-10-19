"""The per-leaf ownership record of a tuple SOURCE at an owning sink.

Every owning tuple sink sema checks (a return, an `Own` or `Own`-element
argument, a field or container store, a container literal element, an
owned-element yield, a captured return) registers the source it takes and
its slot (`TupleSinkRead`); once the body's ownership fact is settled, one
builder (`TypeCompatibility.build_tuple_records`) gives each source one
`TupleSourceRecord`: per reference leaf path, whether the payload arrives
borrowed, whether the slot owns that leaf, and what the sink does with it.
The record is keyed by the canonical source node (`peel_value_wrappers`),
so a coercion or a walrus wrapped around the source reads the same record.
An absent record means the source was never checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..parse import TpyExpr
from ..typesys import TpyType
from ..value_category import peel_value_wrappers
from .alias_rebind import ElemPath

if TYPE_CHECKING:
    from .compatibility import TupleSink
    from .context import SemanticContext


@dataclass(frozen=True)
class TupleSinkRead:
    """One owning sink's registration: the `source` as written (a walrus
    is its target name's read), the `slot` it fills, the `sink` kind (None:
    an `Own` slot, a return's or an argument's), and for a NAME read what
    only its site knows -- a closure reading a name it `captured`, a lambda
    returning its parameter of declared type `param`, and every slot the
    owned tuple sink check copied it into (`name_checked`, empty when that
    check never took the read). Those slots can differ from `slot`, the
    standing registration's: the `Own` coercion's check and the
    statement's both take the read, in either order."""
    source: TpyExpr
    slot: TpyType
    sink: 'TupleSink | None' = None
    captured: bool = False
    param: TpyType | None = None
    name_checked: tuple[TpyType, ...] = ()


class LeafPermission(Enum):
    """What an owning sink does with one reference leaf of a tuple source.

    PASS: the slot does not own the leaf; a borrow passes as a borrow.
    MOVE: an owned leaf moves in, or the leaf is fresh.
    WARNED: an implicit copy, warned (or rejected for a non-copyable type).
    HEDGED: an open `T` leaf, warned through `defer_own_copy_verdict`.
    EXPLICIT: copied under `copy()`.
    UNDECIDED: no verdict yet; a consumer must not act on the leaf."""
    PASS = "pass"
    MOVE = "move"
    WARNED = "warned"
    HEDGED = "hedged"
    EXPLICIT = "explicit"
    UNDECIDED = "undecided"

    @property
    def copies(self) -> bool:
        """The sink copies the leaf implicitly (the copy its warning says)."""
        return self in (LeafPermission.WARNED, LeafPermission.HEDGED)


@dataclass(frozen=True)
class Leaf:
    borrowed: bool
    slot_owns: bool
    permission: LeafPermission


@dataclass(frozen=True)
class TupleSourceRecord:
    """`leaves`: every reference leaf path of the source at its slot. A
    select's verdict sits on the select's record; `arms` holds each arm's
    own record (its own ownership; an arm never warns on its own)."""
    leaves: Mapping[ElemPath, Leaf]
    arms: tuple['TupleSourceRecord', ...] = field(default=())

    def copied_top_level(self) -> frozenset[int]:
        """The top-level elements holding a leaf the sink copies
        implicitly: for a source other than a name, its
        `own_element_copies` entry -- the copies the sink declares by
        top-level element, which the lowering builds."""
        return frozenset(p[0] for p, leaf in self.leaves.items()
                         if leaf.permission.copies)


def tuple_source_record(ctx: 'SemanticContext',
                        expr: TpyExpr | None) -> TupleSourceRecord | None:
    """The record an owning sink published for tuple source `expr` (looked
    up by its canonical node, as it was keyed); None when no sink checked
    it."""
    if expr is None:
        return None
    return ctx.tuple_records.get(peel_value_wrappers(expr))


def publish_tuple_record(ctx: 'SemanticContext', expr: TpyExpr,
                         record: TupleSourceRecord) -> None:
    ctx.tuple_records[peel_value_wrappers(expr)] = record
