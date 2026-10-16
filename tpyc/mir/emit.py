"""MIR built from fragments: the lowering (`lower.py`) builds the body
bottom-up as `Fragment`s while it checks the THIR body -- each expression,
each statement, the body itself (the root fragment). Regions,
borrow-operation points, write modes and every id are handles until the
body is complete; assembly (`assemble`, `_Assembly`) fixes them and
numbers the body canonically."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum, auto

from ..typesys import BOOL, TpyType
from ..identity_map import IdentityMap
from .call_contract import MIRCallSummary
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRClass, MIRConstant, MIRDeref, MIRFunction,
    MIRGoto, MIRHolding, MIRNot, MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRPoint, MIRRead, MIRReceiverInit,
    MIRRecordLayout, MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode, MIRRegion, MIRRegionId, MIRReturn,
    MIRRvalue, MIRShape, MIRSlot, MIRSlotId, MIRSlotKind, MIRStatement, MIRStorageDuration, MIRTerminator,
    MIRTupleInitialization, MIRValidationError, member_init_operands, source_order, statement_target,
)
from .shapes import place_shape
from .validate import body_may_raise, statement_reads, successors, validate_function


class MIRFallthroughError(Exception):
    """A non-void body's end is reachable; the lowering refuses it at the
    body it checked."""


def scalar_shape(typ: TpyType) -> MIRShape:
    """An inert leaf held by value: an expression's scalar result."""
    return MIRShape(typ, MIRClass.LEAF)


def owned_storage_shape(typ: TpyType) -> MIRShape:
    """Owned-leaf storage held in place."""
    return MIRShape(typ, MIRClass.OWNED_LEAF)


def owned_holder_shape(typ: TpyType) -> MIRShape:
    """A readonly holder of owned-leaf storage."""
    return MIRShape(typ, MIRClass.OWNED_LEAF, MIRHolding.POINTER, True)


def view_holder_shape(typ: TpyType) -> MIRShape:
    """A readonly holder typed by a view: what it views is owned-leaf storage."""
    return MIRShape(typ, MIRClass.VIEW, MIRHolding.POINTER, True)


class RegionHandle:
    """A region of the body before assembly numbers it: the body, a
    statement scope, or a full expression, under the region it opened in.
    A full expression that owns no temporaries dissolves: what it holds
    lives in the region around it. A fragment's own region is a dissolved
    handle whose parent is the region it is spliced into."""
    __slots__ = ("parent", "entry", "dissolved")

    def __init__(self, parent: 'RegionHandle | None', entry: '_Block | None', dissolved: bool = False) -> None:
        self.parent = parent
        self.entry = entry
        self.dissolved = dissolved

    @property
    def is_body(self) -> bool:
        return self.resolved().parent is None

    def resolved(self) -> 'RegionHandle':
        region = self
        while region.dissolved:
            region = region.parent
        return region


# The storage of the scope a slot is spliced into: the body's when that is
# the body region, else that region (`Fragment.scoped_duration`).
_SCOPED = object()


@dataclass(eq=False)
class _SlotRecord:
    """A slot while the body is built, frozen to a `MIRSlot` at assembly:
    its storage duration and residence are region handles until then, a
    global's access is its writability, known once every use is checked.
    `loc` is where it is declared, which orders the slots no statement
    names."""
    id: MIRSlotId
    shape: MIRShape
    kind: MIRSlotKind
    name: str | None = None
    alias_source: MIRPlace | None = None
    global_id: object = None
    storage_duration: object = None
    residence: RegionHandle | None = None
    record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT
    passing: object = None
    writable: bool | None = None
    loc: object = None

    @property
    def type(self) -> TpyType:
        return self.shape.type


class _WriteRule(Enum):
    """What decides a pending write's mode (`_PendingWrite`)."""
    RECORD = auto()
    PAYLOAD = auto()
    OWNED = auto()


@dataclass(frozen=True, eq=False)
class _PendingWrite:
    """A storage write whose mode assembly decides: `record` / `payload`
    from the region the write was decided in (an initialization in a
    nested region is per entry) -- a declaration decides it in its scope,
    though the write runs inside its initializer's full expression --,
    `owned` from the target's duration."""
    rule: _WriteRule
    region: RegionHandle | None = None
    target: MIRSlotId | None = None


def _extend(into: list, items: list) -> list:
    """`into` followed by `items`; a spliced fragment's lists are dead
    after the splice, so an empty `into` takes them over -- without it a
    left-nested chain of N splices copies O(N^2) entries."""
    if not into:
        return items
    into.extend(items)
    return into


# A borrow operation at its block's terminator rather than at a statement.
_TERMINATOR = object()


@dataclass(eq=False)
class _Block:
    # None for a fragment's head, whose statements run in the block it is spliced into.
    id: MIRBlockId | None
    region: RegionHandle
    statements: list[MIRStatement] = field(default_factory=list)
    terminator: MIRTerminator | None = None
    # A constant branch's two arms: assembly numbers both, prunes the one not taken.
    arms: tuple[MIRBlockId, MIRBlockId] | None = None
    # Borrow operations waiting for the statement (or terminator) they name.
    pending: list[object] = field(default_factory=list)


class Fragment:
    """MIR built bottom-up before the block it runs in is known: its
    statements in order (the blocks a select or a short-circuit opens
    included), the slots and blocks it adds, the backings it publishes,
    and its value -- `result`, a slot, a place or an rvalue in the form its
    consumer asked for -- with the facts its consumers read of it. Its region
    is a handle on the region it is spliced into.

    The body itself is the root fragment: its head is the entry block, and
    every other fragment's statements, slots and backings reach the body
    only when spliced, so a dropped fragment (a dead statement's) leaves
    nothing behind. Named slots are created where the lowering binds them
    and enter the body where the statement declaring them is placed
    (`adopt`). After `splice` a child's lists may be shared with its target
    and the child is dead (`spliced`)."""

    def __init__(self, root: 'Fragment | None' = None, *, body: MIRBodyId | None = None,
                 layouts: Callable[[], Mapping[TpyType, MIRRecordLayout]] | None = None) -> None:
        self.blocks: list[_Block] = []
        self.slots: list[_SlotRecord] = []
        self.spliced = False
        if root is None:
            self.root = self
            self.body = body
            self.layouts = layouts if layouts is not None else dict
            self.records: dict[MIRSlotId, _SlotRecord] = {}
            self.block_count = 0
            self.constant_bools: dict[MIRSlotId, bool] = {}
            # Optional arguments built empty for their call (`MIRCall.absent`).
            self.empty_arguments: set[MIRSlotId] = set()
            # By the THIR node a backing is published under, and each borrow
            # operation's handles: the block and the statement object it precedes.
            self.backing_places: IdentityMap[object, MIRPlace] = IdentityMap()
            self.borrow_operations: IdentityMap[object, tuple[MIRPoint, ...]] = IdentityMap()
            self.operation_handles: IdentityMap[object, list[tuple[_Block, object]]] = IdentityMap()
            self.region = self.body_region = RegionHandle(None, None)
            self.regions: list[RegionHandle] = [self.region]
            self.head = self.block()
            self.region.entry = self.head
        else:
            self.root = root
            self.body = root.body
            self.region = RegionHandle(None, None, dissolved=True)
            self.head = _Block(None, self.region)
            self.backings: list[tuple[object, MIRPlace]] = []
            # The scopes it opens, the borrow operations it registers and the
            # statements they name: the body's once it is spliced into it.
            self.regions = []
            self.operations: list[object] = []
            self.claims: list[tuple[object, _Block, object]] = []
        self.current: _Block | None = self.head
        # The value and the facts known of it; each producer sets
        # the attributes its consumers read.
        self.result: object = None
        self.type: TpyType | None = None
        self.loc: object = None
        # A place it denotes, and whether reading through it may raise (an
        # unproven element subscript on the way).
        self.place: MIRPlace | None = None
        self.raises = False
        # The shape of what a place denotes: a field's, an element's.
        self.shape: MIRShape | None = None
        # The holder slot the value already is, and the shape of a holder
        # of it when it is not (a view, a record source, a container).
        self.held: MIRSlotId | None = None
        self.holder_shape: MIRShape | None = None
        self.readonly = False
        self.members: tuple[MIRShape, ...] = ()
        # A view's holder type; a tuple's layout; a call's summary; the slot
        # whose own storage the value is (a move out).
        self.view: TpyType | None = None
        self.layout: object = None
        self.summary: MIRCallSummary | None = None
        self.storage: MIRSlotId | None = None
        # As one full expression: whether it owns temporaries, which a region
        # of its own ends, and where it is.
        self.needs_region = False
        self.expression_loc: object = None
        # The refusal of a statement it builds that the body may refuse only
        # where it reaches it (a declaration THIR placed no storage for): the
        # first one it reaches, the body's once it is spliced into it.
        self.refusal: tuple[object, str] | None = None

    def fragment(self) -> 'Fragment':
        return Fragment(self.root)

    # --- container place facts -----------------------------------------------

    @property
    def element(self) -> MIRShape:
        """What iterating the container yields: a list / set / Array element, a dict key."""
        return self.members[0]

    @property
    def keyed(self) -> bool:
        """A dict: a key addresses the value member."""
        return len(self.members) > 1

    @property
    def subscript(self) -> MIRShape:
        """The member a subscript yields: a dict's value, else the element."""
        return self.members[-1]

    # --- slots and blocks ----------------------------------------------------

    def record(self, shape: MIRShape, kind: MIRSlotKind = MIRSlotKind.TEMPORARY, name: str | None = None, *,
               loc: object = None, alias_source: MIRPlace | None = None, global_id: object = None,
               storage_duration: object = None,
               record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT,
               passing: object = None, writable: bool | None = None) -> MIRSlotId:
        """A slot of the body, not yet placed: `adopt` places it."""
        root = self.root
        sid = MIRSlotId(self.body, len(root.records))
        root.records[sid] = _SlotRecord(sid, shape, kind, name, alias_source=alias_source, global_id=global_id,
                                        storage_duration=storage_duration, record_storage=record_storage,
                                        passing=passing, writable=writable, loc=loc)
        return sid

    def adopt(self, sid: MIRSlotId, storage_duration: object = None) -> MIRSlotId:
        """Place a slot created earlier here, with its storage duration."""
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        record = self.root.records[sid]
        record.storage_duration = storage_duration
        if record.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY):
            if storage_duration is _SCOPED:
                storage_duration = record.storage_duration = self.scope_storage(self.region)
            record.residence = (self.root.body_region if storage_duration is MIRStorageDuration.BODY
                                else storage_duration
                                if record.shape.is_storage and isinstance(storage_duration, RegionHandle)
                                else self.region)
        self.slots.append(record)
        return sid

    def slot(self, shape: MIRShape, kind: MIRSlotKind = MIRSlotKind.TEMPORARY, name: str | None = None, *,
             storage_duration: object = None, **facts: object) -> MIRSlotId:
        return self.adopt(self.record(shape, kind, name, **facts), storage_duration)

    def scope_storage(self, region: RegionHandle) -> object:
        if self is not self.root:
            # Decided where the fragment reaches the body.
            return _SCOPED
        resolved = region.resolved()
        return MIRStorageDuration.BODY if resolved.is_body else resolved

    def scoped_duration(self) -> object:
        """The storage of the current scope: its region, or the body's at top level."""
        return self.scope_storage(self.region)

    @contextmanager
    def scope(self) -> Iterator[None]:
        """A scope opening at the current block: a region of its own."""
        region = self.region
        assert self.current is not None and not self.current.statements
        self.region = RegionHandle(region, self.current)
        self.regions.append(self.region)
        self.current.region = self.region
        try:
            yield
        finally:
            self.region = region

    @contextmanager
    def dissolved(self) -> Iterator[None]:
        """A full expression owning no temporaries: its region dissolves
        into the one around it."""
        region = self.region
        self.region = RegionHandle(region, None, dissolved=True)
        try:
            yield
        finally:
            self.region = region

    @contextmanager
    def full_expression(self, value: 'Fragment') -> Iterator[None]:
        """One full expression (`value`): a region of its own when it owns temporaries."""
        if not value.needs_region:
            with self.dissolved():
                yield
            return
        entry, after = self.block(), self.block()
        self.end(MIRGoto(entry.id, value.expression_loc))
        self.current = entry
        with self.scope():
            yield
            self.end(MIRGoto(after.id, value.expression_loc))
        self.current = after

    def full_expression_value(self, value: 'Fragment') -> MIRSlotId:
        if not value.needs_region:
            with self.dissolved():
                return self.splice(value)
        result = self.slot(scalar_shape(value.type))
        with self.full_expression(value):
            self.write(result, MIRRead(MIRPlace(self.splice(value))), value.expression_loc)
        return result

    def refuse(self, node: object, reason: str) -> None:
        """A refusal of what this fragment builds, raised only if the body reaches it."""
        if self.refusal is None:
            self.refusal = (node, reason)

    def block(self) -> _Block:
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        root = self.root
        block = _Block(MIRBlockId(self.body, root.block_count), self.region)
        root.block_count += 1
        self.blocks.append(block)
        return block

    # --- statements ------------------------------------------------------------

    def append(self, stmt: MIRStatement) -> None:
        """Add a statement to the current block; the borrow operations
        waiting there name it."""
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        block = self.current
        assert block is not None
        for operation in block.pending:
            self.claim(operation, block, stmt)
        block.pending.clear()
        block.statements.append(stmt)

    def claim(self, operation: object, block: _Block, stmt: object) -> None:
        """A borrow operation's point: the statement (or terminator) of `block` it names."""
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        if self is self.root:
            self.operation_handles[operation].append((block, stmt))
        else:
            self.claims.append((operation, block, stmt))

    def write(self, dest: MIRSlotId | MIRPlace, value: MIRRvalue, loc: object,
              storage_write: MIRRecordWrite | MIRPayloadWrite | MIRTupleInitialization | _PendingWrite | None = None
              ) -> None:
        target = MIRPlace(dest) if isinstance(dest, MIRSlotId) else dest
        self.append(MIRAssign(target, value, loc, storage_write))

    def end(self, term: MIRTerminator) -> None:
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        block = self.current
        assert block is not None and block.terminator is None
        for operation in block.pending:
            self.claim(operation, block, _TERMINATOR)
        block.pending.clear()
        block.terminator = term
        self.current = None

    def borrow_operation(self, operation: object) -> None:
        """The operation's point is the next statement of the current block,
        or its terminator; assembly resolves it to a `MIRPoint`."""
        assert self.current is not None
        self.register(operation)
        self.current.pending.append(operation)

    def register(self, operation: object) -> None:
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        if self is not self.root:
            self.operations.append(operation)
        elif operation not in self.operation_handles:
            self.operation_handles[operation] = []

    def publish(self, key: object, place: MIRPlace) -> None:
        """A backing published under the THIR node it stores."""
        assert not self.spliced, "a spliced fragment's lists belong to its target"
        if self is self.root:
            self.backing_places[key] = place
        else:
            self.backings.append((key, place))

    def splice(self, fragment: 'Fragment') -> object:
        """Run `fragment` here: its statements in the current block, its
        blocks and slots this fragment's; its result."""
        assert fragment.root is self.root and not fragment.spliced
        fragment.spliced = True
        fragment.region.parent = self.region
        into = self.current
        head = fragment.head
        if head.statements:
            if into.pending:
                self.append(head.statements[0])
                into.statements.extend(head.statements[1:])
            else:
                into.statements = _extend(into.statements, head.statements)
        # The borrow operations its head names are the block's it runs in.
        root = self is self.root
        if root:
            for operation in fragment.operations:
                self.register(operation)
        else:
            self.operations = _extend(self.operations, fragment.operations)
        for operation, block, stmt in fragment.claims:
            self.claim(operation, into if block is head else block, stmt)
        into.pending.extend(head.pending)
        if head.terminator is not None:
            self.end(head.terminator)
            into.arms = head.arms
            self.current = fragment.current
        self.regions = _extend(self.regions, fragment.regions)
        self.blocks = _extend(self.blocks, fragment.blocks)
        if root:
            for record in fragment.slots:
                if record.storage_duration is _SCOPED:
                    record.storage_duration = self.scope_storage(record.residence)
            self.slots.extend(fragment.slots)
            for key, place in fragment.backings:
                self.publish(key, place)
        else:
            # Durations and backings are decided where the fragment reaches the body.
            self.slots = _extend(self.slots, fragment.slots)
            self.backings = _extend(self.backings, fragment.backings)
        if self.refusal is None:
            self.refusal = fragment.refusal
        return fragment.result

    # --- values ----------------------------------------------------------------

    def leaf(self, typ: TpyType, value: MIRRvalue, loc: object) -> MIRSlotId:
        """A scalar result slot written from `value`."""
        dest = self.slot(scalar_shape(typ))
        self.write(dest, value, loc)
        # Only these expression temporaries have one definition; locals and select results can change.
        if typ == BOOL:
            constants = self.root.constant_bools
            match value:
                case MIRConstant(value=constant) if type(constant) is bool:
                    constants[dest] = constant
                case MIRNot(operand=operand) if operand in constants:
                    constants[dest] = not constants[operand]
        return dest

    def holder(self, shape: MIRShape, value: MIRRvalue, loc: object) -> MIRSlotId:
        """A holder of a value already evaluated."""
        holder = self.slot(shape)
        self.write(holder, value, loc)
        return holder

    def branch(self, cond: MIRSlotId, yes: MIRBlockId, no: MIRBlockId, loc: object) -> None:
        constants = self.root.constant_bools
        if cond in constants:
            block = self.current
            self.end(MIRGoto(yes if constants[cond] else no, loc))
            block.arms = (yes, no)
        else:
            self.end(MIRBranch(cond, yes, no, loc))

    def select(self, cond: MIRSlotId, then: 'MIRSlotId | Fragment', otherwise: 'MIRSlotId | Fragment',
               typ: TpyType, loc: object) -> MIRSlotId:
        dest = self.slot(scalar_shape(typ))
        yes, no, join = self.block(), self.block(), self.block()
        self.branch(cond, yes.id, no.id, loc)
        for block, arm in ((yes, then), (no, otherwise)):
            self.current = block
            value = arm if isinstance(arm, MIRSlotId) else self.splice(arm)
            self.write(dest, MIRRead(MIRPlace(value)), loc)
            self.end(MIRGoto(join.id, loc))
        self.current = join
        return dest

    def storage_steps(self, place: MIRPlace) -> tuple[MIRDeref, ...]:
        """The dereference from a place to the storage its value points at:
        none for storage held in place."""
        shape = place_shape(place, self.root.records, self.root.layouts())
        return (MIRDeref(),) if shape.is_pointer else ()

    # --- write modes and temporaries ---------------------------------------------

    def payload_write(self, inline: bool, mode: MIRPayloadWriteMode) -> MIRPayloadWrite | _PendingWrite | None:
        """The payload write of a wrapper holding its payload inline (a
        declaration's `shape.scalar_wrapper`, an assignment's `inline_payload`)."""
        if not inline:
            return None
        if mode is MIRPayloadWriteMode.INITIALIZE:
            return _PendingWrite(_WriteRule.PAYLOAD, self.region)
        return MIRPayloadWrite(mode)

    def initial_write(self) -> _PendingWrite:
        """A record storage initialization: once in the body region, per
        entry in a nested one (decided at assembly)."""
        return _PendingWrite(_WriteRule.RECORD, self.region)

    def owned_initialization(self, dest: MIRSlotId) -> _PendingWrite:
        """Owned storage's initialization, by its duration (decided at assembly)."""
        return _PendingWrite(_WriteRule.OWNED, target=dest)

    def initialize(self, temporaries: tuple[object, ...]) -> None:
        """Build placed temporaries where the temporary plan initializes them."""
        for temporary in temporaries:
            write = (MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN) if temporary.optional
                     else self.initial_write())
            self.write(temporary.storage, self.splice(temporary.value), temporary.loc, write)
            self.write(temporary.holder, MIRBorrow(MIRPlace(temporary.storage)), temporary.loc)


def assemble(body: Fragment, *, void: bool, return_type: TpyType, kind: object, callees: tuple[object, ...],
             result: object, layouts: Mapping[TpyType, MIRRecordLayout],
             receiver_init: MIRReceiverInit | None = None) -> MIRFunction:
    """The body built, closed and numbered: a reachable end returns (a
    non-void body's refuses), then assembly."""
    blocks = {b.id: b for b in body.blocks}
    reachable: set[MIRBlockId] = set()
    pending = [body.head.id]
    while pending:
        bid = pending.pop()
        if bid in reachable:
            continue
        reachable.add(bid)
        term = blocks[bid].terminator
        if term is not None:
            pending.extend(successors(term))
    if body.current is not None and body.current.id in reachable:
        if not void:
            raise MIRFallthroughError("non-void fallthrough")
        body.end(MIRReturn())
    return _Assembly(body, receiver_init, reachable).function(return_type, kind, callees, result, layouts)


def _structure(value: object) -> bool:
    """A MIR node whose fields may name slots or blocks; a call summary and
    a shape name neither."""
    return (is_dataclass(value) and not isinstance(value, (type, MIRCallSummary, MIRShape))
            and type(value).__module__.startswith("tpyc.mir"))


def slot_occurrences(value: object, out: list[MIRSlotId]) -> None:
    """Every slot `value` names, in field order (a statement, a terminator,
    an entry initialization)."""
    if isinstance(value, MIRSlotId):
        out.append(value)
    elif isinstance(value, tuple):
        for item in value:
            slot_occurrences(item, out)
    elif _structure(value):
        for f in fields(value):
            slot_occurrences(getattr(value, f.name), out)


def _region_depth(region: RegionHandle) -> int:
    depth = 0
    while region.parent is not None:
        region = region.parent.resolved()
        depth += 1
    return depth


class _Assembly:
    """The built body numbered and frozen: borrow operations resolved to
    points, blocks numbered depth-first from the entry over every arm
    (a constant branch's pruned arm included, so a point in it keeps an
    id the function no longer holds), regions by their entry block,
    parameters first in signature order, every other slot by its first
    occurrence, the slots no statement names by kind, declaration site and
    name; then pruning, the write modes, the frozen slots."""

    def __init__(self, emitter: Fragment, receiver_init: MIRReceiverInit | None,
                 reachable: set[MIRBlockId]) -> None:
        self.emitter = emitter
        self.body = emitter.body
        self.receiver_init = receiver_init
        self.reachable = reachable
        self.by_id = {b.id: b for b in emitter.blocks}
        self.block_ids = self.number_blocks()
        self.region_ids = self.number_regions()
        self.slot_ids = self.number_slots()

    # --- numbering ---------------------------------------------------------

    def number_blocks(self) -> dict[MIRBlockId, MIRBlockId]:
        blocks, by_id = self.emitter.blocks, self.by_id
        order: list[_Block] = []
        seen: set[MIRBlockId] = set()

        def arms(block: _Block) -> tuple[MIRBlockId, ...]:
            if block.arms is not None:
                return block.arms
            return successors(block.terminator) if block.terminator is not None else ()
        stack = [iter((self.emitter.head.id,))]
        while stack:
            bid = next(stack[-1], None)
            if bid is None:
                stack.pop()
                continue
            if bid in seen:
                continue
            seen.add(bid)
            order.append(by_id[bid])
            stack.append(iter(arms(by_id[bid])))
        order.extend(b for b in blocks if b.id not in seen)
        return {b.id: MIRBlockId(self.body, index) for index, b in enumerate(order)}

    def number_regions(self) -> dict[RegionHandle, MIRRegionId]:
        regions = sorted(self.emitter.regions, key=lambda r: (self.block_ids[r.entry.id].index, _region_depth(r)))
        return {region: MIRRegionId(self.body, index) for index, region in enumerate(regions)}

    def number_slots(self) -> dict[MIRSlotId, MIRSlotId]:
        records = self.emitter.slots
        placed = {r.id: r for r in records}
        order: dict[MIRSlotId, None] = {r.id: None for r in records if r.kind is MIRSlotKind.PARAMETER}
        found: list[MIRSlotId] = []
        slot_occurrences(self.receiver_init, found)
        for block in sorted(self.emitter.blocks, key=lambda b: self.block_ids[b.id].index):
            for stmt in block.statements:
                slot_occurrences(stmt, found)
            slot_occurrences(block.terminator, found)
        for sid in found:
            if sid not in placed:
                raise MIRValidationError("statement names a slot the body never placed")
            order.setdefault(sid)
        added = True
        while added:
            added = False
            for sid in list(order):
                source = placed[sid].alias_source
                if source is not None and source.root not in order:
                    order[source.root] = None
                    added = True
        kinds = {kind: index for index, kind in enumerate(MIRSlotKind)}
        # The slots no statement names: by kind, then where they are declared
        # (an undeclared one last), then by name.
        rest = sorted((r for r in records if r.id not in order),
                      key=lambda r: (kinds[r.kind], source_order(r.loc), r.name or "", repr(r.global_id)))
        for record in rest:
            order[record.id] = None
        return {sid: MIRSlotId(self.body, index) for index, sid in enumerate(order)}

    # --- remapping ---------------------------------------------------------

    def region(self, region: RegionHandle) -> MIRRegionId:
        found = self.region_ids.get(region.resolved())
        if found is None:
            raise MIRValidationError("region handle names no region of the body")
        return found

    def remap(self, value: object) -> object:
        """`value` under the final numbering, a pending write mode decided."""
        if isinstance(value, MIRSlotId):
            return self.slot_ids[value]
        if isinstance(value, MIRBlockId):
            return self.block_ids[value]
        if isinstance(value, tuple):
            return tuple(self.remap(item) for item in value)
        if isinstance(value, _PendingWrite):
            return self.write_mode(value)
        if _structure(value):
            changes = {}
            for f in fields(value):
                item = getattr(value, f.name)
                mapped = self.remap(item)
                if mapped is not item:
                    changes[f.name] = mapped
            return replace(value, **changes) if changes else value
        return value

    def write_mode(self, pending: _PendingWrite) -> MIRRecordWrite | MIRPayloadWrite:
        match pending.rule:
            case _WriteRule.RECORD:
                return MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE if pending.region.is_body
                                      else MIRRecordWriteMode.INITIALIZE_REGION)
            case _WriteRule.PAYLOAD:
                return MIRPayloadWrite(MIRPayloadWriteMode.INITIALIZE if pending.region.is_body
                                       else MIRPayloadWriteMode.INITIALIZE_REGION)
            case _WriteRule.OWNED:
                duration = self.emitter.records[pending.target].storage_duration
                return MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION if isinstance(duration, RegionHandle)
                                      else MIRRecordWriteMode.INITIALIZE_ONCE)

    def duration(self, duration: object) -> object:
        return self.region(duration) if isinstance(duration, RegionHandle) else duration

    def freeze(self, record: _SlotRecord) -> MIRSlot:
        shape = record.shape
        if record.writable is False and not shape.readonly:
            # A global no use writes is read through a readonly handle.
            shape = replace(shape, readonly=True)
        return MIRSlot(self.slot_ids[record.id], shape, record.kind, record.name,
                       alias_source=self.remap(record.alias_source), global_id=record.global_id,
                       storage_duration=self.duration(record.storage_duration),
                       residence=self.region(record.residence) if record.residence is not None else None,
                       record_storage=record.record_storage, passing=record.passing)

    # --- the function ------------------------------------------------------

    def operations(self) -> IdentityMap[object, tuple[MIRPoint, ...]]:
        """Each borrow operation at the point of the statement it precedes,
        or of its block's terminator."""
        emitter = self.emitter
        if any(block.pending for block in emitter.blocks):
            raise MIRValidationError("borrow operation names no statement")
        points: IdentityMap[object, tuple[MIRPoint, ...]] = IdentityMap()
        indices: IdentityMap[_Block, IdentityMap[object, int]] = IdentityMap()

        def index_of(block: _Block, stmt: object) -> int | None:
            if stmt is _TERMINATOR:
                return len(block.statements)
            if block not in indices:
                index = indices[block] = IdentityMap()
                for i, s in enumerate(block.statements):
                    if s not in index:
                        index[s] = i
            index = indices[block]
            return index[stmt] if stmt in index else None

        for operation, handles in emitter.operation_handles.items():
            resolved = []
            for block, stmt in handles:
                index = index_of(block, stmt)
                if index is None:
                    raise MIRValidationError("borrow operation statement is not in its block")
                resolved.append(MIRPoint(self.block_ids[block.id], index))
            if not resolved:
                raise MIRValidationError("borrow operation names no statement")
            points[operation] = tuple(resolved)
        return points

    def function(self, return_type: TpyType, kind: object, callees: tuple[object, ...], result: object,
                 layouts: Mapping[TpyType, MIRRecordLayout]) -> MIRFunction:
        emitter = self.emitter
        operations = self.operations()
        emitter.borrow_operations = operations
        for key, place in list(emitter.backing_places.items()):
            emitter.backing_places[key] = self.remap(place)
        blocks = []
        for b in sorted(emitter.blocks, key=lambda b: self.block_ids[b.id].index):
            if b.id not in self.reachable:
                continue
            assert b.terminator is not None
            blocks.append(MIRBlock(self.block_ids[b.id], self.remap(tuple(b.statements)), self.remap(b.terminator),
                                   self.region(b.region)))
        receiver_init = self.remap(self.receiver_init)
        frozen = {slot.id: slot for slot in map(self.freeze, emitter.slots)}
        reachable_blocks = {b.id for b in blocks}
        regions = tuple(MIRRegion(self.region_ids[r], None if r.parent is None else self.region(r.parent),
                                  self.block_ids[r.entry.id])
                        for r in sorted(self.region_ids, key=lambda r: self.region_ids[r].index)
                        if self.block_ids[r.entry.id] in reachable_blocks)
        records = tuple(layouts.values())
        fn = MIRFunction(self.body, return_type, _reachable_slots(frozen, blocks, receiver_init), tuple(blocks),
                         self.block_ids[emitter.head.id], records, receiver_init, kind, regions,
                         callees, result,
                         body_may_raise(blocks, frozen, {r.type: r for r in records}, receiver_init))
        validate_function(fn)
        return fn


def _reachable_slots(slots: Mapping[MIRSlotId, MIRSlot], blocks: list[MIRBlock],
                     receiver: MIRReceiverInit | None) -> tuple[MIRSlot, ...]:
    retained = {s.id for s in slots.values() if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
    if receiver is not None:
        retained.add(receiver.receiver)
        retained.update(member_init_operands(receiver.fields))
    for block in blocks:
        for stmt in block.statements:
            if (target := statement_target(stmt)) is not None:
                retained.add(target.root)
            retained.update(statement_reads(stmt))
            if (isinstance(stmt, MIRAssign) and isinstance(stmt.storage_write, MIRRecordWrite)
                    and stmt.storage_write.rebind_owner is not None):
                retained.add(stmt.storage_write.rebind_owner)
        match block.terminator:
            case MIRBranch(condition=condition):
                retained.add(condition)
            case MIRReturn(value=value) if value is not None:
                retained.add(value)
    pending = list(retained)
    while pending:
        source = slots[pending.pop()].alias_source
        if source is not None and source.root not in retained:
            retained.add(source.root)
            pending.append(source.root)
    # Pruned blocks leave gaps in the numbering; consumers key by identity.
    return tuple(sorted((s for s in slots.values() if s.id in retained), key=lambda s: s.id.index))
