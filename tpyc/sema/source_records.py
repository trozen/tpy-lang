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

Whether a leaf COPIES comes from the source facts the builder reads. Whether
the user was TOLD of that copy comes from the copy diagnostics: every check
that warns of a copy of a source into an owning slot records a
`CopyDeclaration` linked to its `Diagnostic` by identity, and one pass at
the end of module analysis (`finalize_source_records`), once every
diagnostic has its final fate -- held and dropped, deduplicated, replaced
by an instantiation's error, truncated by a rejected trial -- resolves each
copying leaf from the declarations whose diagnostic still stands.

No record means the source was never checked. A record with no leaf for a
value-bounded element (`T: ValueType`) means its copy is proven harmless.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..diagnostics import Diagnostic
from ..identity_map import IdentityMap, IdentitySet
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
    WARNED: an implicit copy, and a standing copy warning says so.
    HEDGED: an implicit copy of an open `T` leaf, and the standing
        definition-time hedge (`defer_own_copy_verdict`) says so.
    EXPLICIT: copied under `copy()`.
    UNDECIDED: a copy no standing diagnostic tells, or no verdict yet; a
        consumer must not act on the leaf."""
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
    # Set by the builder on a leaf the source facts say COPIES, until
    # `finalize_source_records` resolves its permission from the standing
    # declarations: each node of the leaf's source chain (the registered
    # source, then a literal member under it) with the leaf's path
    # relative to that node. On a MOVE leaf, the nodes whose told copy
    # takes the move back. Empty on every leaf of a finalized record.
    told_by: tuple[tuple[TpyExpr, ElemPath], ...] = field(
        default=(), compare=False, repr=False)


@dataclass(frozen=True)
class SourceRecord:
    """`leaves`: every reference leaf path of the source at its slot. A
    select's verdict sits on the select's record; `arms` holds each arm's
    own record (its own ownership; an arm never warns on its own)."""
    leaves: Mapping[ElemPath, Leaf]
    arms: tuple['SourceRecord', ...] = field(default=())

    def copied_top_level(self) -> frozenset[int]:
        """The top-level elements of a tuple source holding a leaf the sink
        copies implicitly: for a source other than a name, its
        `own_element_copies` entry -- the copies the sink declares by
        top-level element, which the lowering builds. A scalar's one leaf
        (path `()`) is no element."""
        return frozenset(p[0] for p, leaf in self.leaves.items()
                         if p and leaf.permission.copies)


@dataclass(frozen=True, eq=False)
class CopyDeclaration:
    """A copy diagnostic's statement that the leaves of `source` (its
    canonical node) at and under `path` are copied into an owning slot:
    WARNED for a warning, HEDGED for an open payload's definition-time
    hedge. It counts only while `diag` is in the module's final
    diagnostics, by identity (the `OwnCopyObligation.diag` precedent)."""
    source: TpyExpr
    path: ElemPath
    kind: LeafPermission
    diag: Diagnostic


def declare_copy(ctx: 'SemanticContext', source: TpyExpr | None,
                 path: ElemPath, kind: LeafPermission,
                 diag: Diagnostic | None) -> None:
    """Record that `diag` tells the copy of `source`'s leaves at `path`."""
    if source is None or diag is None:
        return
    ctx.copy_declarations.append(
        CopyDeclaration(peel_value_wrappers(source), path, kind, diag))


def source_record(ctx: 'SemanticContext',
                  expr: TpyExpr | None) -> SourceRecord | None:
    """The record an owning sink published for source `expr` (looked up by
    its canonical node, as it was keyed); None when no sink checked it."""
    if expr is None:
        return None
    return ctx.source_records.get(peel_value_wrappers(expr))


def publish_source_record(ctx: 'SemanticContext', expr: TpyExpr,
                          record: SourceRecord) -> None:
    ctx.source_records[peel_value_wrappers(expr)] = record


def finalize_source_records(ctx: 'SemanticContext') -> None:
    """Resolve every copying leaf of the module's records from the copy
    declarations whose diagnostic stands in the final `ctx.diagnostics`: a
    standing warning makes it WARNED, a standing hedge alone HEDGED, and
    nothing standing leaves the copy UNDECIDED. Runs once, after the last
    diagnostic-emitting step of the module."""
    standing: IdentitySet = IdentitySet(ctx.diagnostics)
    told: IdentityMap = IdentityMap()
    for d in ctx.copy_declarations:
        if d.diag in standing:
            told.setdefault(d.source, []).append((d.path, d.kind))

    def leaf_told(leaf: Leaf) -> Leaf:
        if not leaf.told_by:
            return leaf
        kinds = {kind for node, sub in leaf.told_by
                 for path, kind in told.get(node, ())
                 if sub[:len(path)] == path}
        perm = (LeafPermission.WARNED if LeafPermission.WARNED in kinds
                else LeafPermission.HEDGED if LeafPermission.HEDGED in kinds
                else LeafPermission.MOVE
                if leaf.permission is LeafPermission.MOVE
                else LeafPermission.UNDECIDED)
        return replace(leaf, permission=perm, told_by=())

    def resolve(rec: SourceRecord) -> SourceRecord:
        return SourceRecord({p: leaf_told(leaf)
                             for p, leaf in rec.leaves.items()},
                            tuple(resolve(a) for a in rec.arms))

    for key, rec in list(ctx.source_records.items()):
        ctx.source_records[key] = resolve(rec)
