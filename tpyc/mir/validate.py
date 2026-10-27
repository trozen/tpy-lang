"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque
from collections.abc import Mapping
from dataclasses import replace

from ..identity_map import IdentityMap, IdentitySet
from ..parse import SourceLocation
from ..thir.nodes import COMPARISON_OPS, THIRBorrowedRecord, THIRStubCallee, THIRStubContract
from ..type_def_registry import is_array
from ..thir.scalar_leaves import (
    MIRClass, leaf_constant, modeled_field, owned_constant, owned_value_type,
    primitive_leaf, record_type, value_class,
)
from ..type_def_registry import type_def_of, zero_value_of
from ..typesys import (
    BOOL, INT32, NominalType, TpyType, VoidType,
    certified_primitive_comparison, unwrap_own,
    unwrap_readonly, view_family_of,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRGoto,
    MIRFunction, MIRNot, MIRPlace, MIRRead, MIRReturn, MIRRvalue, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRBodyKind, MIRGlobalId,
    MIRStorageWrite, MIRStorageWriteMode, MIRStorageInit,
    MIRStatement, MIRRegionId, MIREdge, MIRIsAlternative, MIRUnionExtract,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance, MIRRangeAdvance, MIROp, MIRPrint,
    statement_target, place_shape, view_member, MIRWrap, builds_base, initialized_places,
)
from .value import (
    AGGREGATE_CLASSES, CONTAINER_CLASSES, MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRecords, MIRShape,
    MIRValidationError, admitted_members, member_compatible, require_valid as _require, shape_problem, thir_member,
    with_access, SUM_CLASSES, WRAPPER_CLASSES, member_binds, wrapper_compatible, caller_storage,
)
from .coverage import primitive_operand, read_leaf, slot_representation, view_compatible
from .region_flow import MIRRegionFlow, outgoing_edges
from .call_contract import (
    MIRParameterBinding, binds_at, crossed_records, owned_aggregate_result, signature_result,
    parameter_contract_problem, result_problem, summary_problem, MIRCallSummary, MIRPathWalks,
)
from .definitions import MIRAggregateFacts, aggregate_facts, copy_may_raise, owned_aggregate_parameter
from .shapes import (
    REGION_END, walk, MIRContainerElements, MIRDeref, MIRTupleIndex, MIRUnionPayload, construct_members,
    object_prefix, sole_object, sole_payload, store_problem,
)


def handed_storage(slot: MIRSlot, bindings: 'Mapping[MIRSlotId, MIRParameterBinding]') -> bool:
    """An `Own[...]` parameter's aggregate storage (`owned_aggregate_parameter`
    of its binding, whose value the slot holds), the body's own from entry
    to exit (the caller materialized it)."""
    binding = bindings.get(slot.id)
    return (binding is not None and owned_aggregate_parameter(binding) is not None
            and slot.storage_duration is MIRStorageDuration.BODY)


def elements_place(place: MIRPlace) -> bool:
    return bool(place.projections) and isinstance(place.projections[-1], MIRContainerElements)


def reaches_element(place: MIRPlace) -> bool:
    """Whether a place passes through a container's element region: an
    access of it checks an index or key, so it may raise."""
    return any(isinstance(p, MIRContainerElements) for p in place.projections)


class MIRPresenceError(MIRValidationError):
    """A payload access lacks a valid presence proof at its CFG position."""


class MIRDefiniteAssignmentError(MIRValidationError):
    """Structural CFG paths do not establish initialization before a read."""


class MIRRepeatedInitializationError(MIRValidationError):
    """A static place initializes twice within one modeled region activation."""

    def __init__(self, loc: SourceLocation | None) -> None:
        super().__init__("repeated initialization within region activation")
        self.loc = loc


def _region_structure(fn: MIRFunction) -> MIRRegionFlow:
    regions = {r.id: r for r in fn.regions}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(regions) == len(fn.regions), "duplicate region ID")
    if regions:
        roots = [r for r in fn.regions if r.parent is None]
        _require(len(roots) == 1 and roots[0].entry == fn.entry, "invalid body region")
    for region in fn.regions:
        _require(region.id.body == fn.id and region.id.index >= 0
                 and region.entry in blocks and blocks[region.entry].region == region.id,
                 "invalid region identity or entry")
        seen = {region.id}
        parent = region.parent
        while parent is not None:
            _require(parent in regions and parent not in seen, "invalid region ancestry")
            seen.add(parent)
            parent = regions[parent].parent
    for block in fn.blocks:
        _require(block.region in regions if regions else block.region is None, "missing block region")
        _require(isinstance(block.terminator, (MIRGoto, MIRBranch, MIRReturn)), "missing or unknown terminator")
        _require(all(target is None or target in blocks for _, target in outgoing_edges(block.id, block.terminator)),
                 "invalid block target")
    for slot in fn.slots:
        local = slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
        _require(slot.residence in regions if regions and local else slot.residence is None,
                 "invalid binding residence")
        if isinstance(slot.storage_duration, MIRRegionId):
            _require(slot.storage_duration in regions and regions[slot.storage_duration].parent is not None
                     and local and slot.residence == slot.storage_duration,
                     "invalid storage region")
        elif slot.storage_duration is MIRStorageDuration.BODY and regions and local:
            _require(slot.residence == roots[0].id, "body storage has nested residence")
    flow = MIRRegionFlow(fn)
    for transition in flow.edges.values():
        for rid in transition.entered:
            _require(regions[rid].entry == transition.target, "entry into region interior")
    return flow


def operands(value: MIRRvalue) -> tuple[MIRSlotId, ...]:
    """The slots a value reads; a constant operand is none."""
    match value:
        case MIRCall(arguments=arguments):
            return arguments
        case (MIRRead(source=source) | MIRCopy(source=source)
              | MIRUnionExtract(source=source) | MIRBorrow(source=source)
              | MIRIsAlternative(source=source)):
            _require(isinstance(source, MIRPlace), "invalid read place")
            return (source.root,)
        case (MIRAlias(source=source) | MIRMove(source=source)
              | MIRIteratorInit(source=source) | MIRIteratorHasNext(source=source)
              | MIRIteratorRead(source=source) | MIRIteratorAdvance(source=source)
              | MIRRangeAdvance(source=source)):
            return (source,)
        case MIRWrap(source=source):
            _require(source is None or isinstance(source, MIRPlace), "invalid read place")
            return () if source is None else (source.root,)
        case MIRConstruct(fields=fields):
            return tuple(f for f in fields if not isinstance(f, MIRConstant))
        case MIRCompare(left=left, right=right):
            return (left, right)
        case MIRNot(operand=operand):
            return (operand,)
        case MIROp(operands=values):
            return values
        case MIRConstant():
            return ()
        case _:
            raise MIRValidationError("unknown rvalue")


def successors(term: MIRGoto | MIRBranch | MIRReturn) -> tuple[MIRBlockId, ...]:
    match term:
        case MIRGoto(target=target):
            return (target,)
        case MIRBranch(then=then, otherwise=otherwise):
            return (then, otherwise)
        case MIRReturn():
            return ()
        case _:
            raise MIRValidationError("missing or unknown terminator")


def reachable_blocks(fn: MIRFunction) -> frozenset[MIRBlockId]:
    """The blocks a path from the entry reaches, by terminator alone."""
    blocks = {block.id: block for block in fn.blocks}
    reached: set[MIRBlockId] = set()
    pending = [fn.entry]
    while pending:
        bid = pending.pop()
        if bid not in reached:
            reached.add(bid)
            pending.extend(successors(blocks[bid].terminator))
    return frozenset(reached)


def statement_reads(stmt: MIRStatement) -> tuple[MIRSlotId, ...]:
    match stmt:
        case MIRCallStmt(call=call):
            return operands(call)
        case MIRPrint(arguments=arguments):
            return arguments
        case MIRStorageInit():
            return ()
        case MIRAssign(target=target, value=value):
            return (*operands(value), target.root) if target.projections else operands(value)
        case _:
            raise MIRValidationError("unknown instruction")


def statement_may_raise(stmt: MIRStatement, slots: 'Mapping[MIRSlotId, MIRSlot]', records: MIRRecords) -> bool:
    """Whether `stmt` can exit the body by exception: a raising operation, a
    copy or constant materialization into owned storage whose allocation
    can throw (`TypeDef.copy_may_raise`), a container literal, an element
    access that checks its index or key, a call whose summary may raise, or
    a print, whose formatting allocates."""
    match stmt:
        case MIRCallStmt(call=call):
            return call.may_raise
        case MIRPrint():
            return True
        case MIRAssign(target=target) if reaches_element(target):
            # An element written in place may raise: its index may be out of range.
            return True
        case MIRAssign(target=target, value=value):
            # A global target has no slot shape; only a constant reads the shape.
            shape = place_shape(target, slots, records) if target.root in slots else None
            return value_may_raise(value, shape, records)
        case MIRStorageInit():
            return False
        case _:
            raise MIRValidationError("unknown instruction")


def value_may_raise(value: MIRRvalue, target: MIRShape | None, records: MIRRecords) -> bool:
    """Whether writing `value` into a value of shape `target` can exit by
    exception: a raising operation, call, copy, element access or literal
    (its own flag), or a constant materialized into an owned leaf's buffer
    (`TypeDef.copy_may_raise`; a view or a scalar takes it as is)."""
    match value:
        case (MIROp(may_raise=may_raise) | MIRCall(may_raise=may_raise) | MIRCopy(may_raise=may_raise)
              | MIRConstruct(may_raise=may_raise) | MIRRead(may_raise=may_raise)
              | MIRBorrow(may_raise=may_raise)):
            return may_raise
        case MIRConstant():
            return target is not None and target.leaf_storage and bool(type_def_of(target.type).copy_may_raise)
    return False


def owned_leaf_place_type(place: MIRPlace, slots: 'Mapping[MIRSlotId, MIRSlot]',
                          records: 'Mapping[TpyType, MIRRecordLayout]') -> TpyType | None:
    """The owned leaf `place` denotes as a whole buffer -- the body's own
    storage, what a borrowed holder points at, an owned-leaf record field
    or element -- or None."""
    if place.root not in slots:
        return None
    shape = place_shape(place, slots, records)
    return shape.type if shape.leaf_storage else None


def copies_into(source: TpyType, target: TpyType) -> bool:
    """Owned storage takes a copy of its own type, or of any member of its
    view family a view of it could hold (a `String`, or what a view views)."""
    family = view_family_of(target)
    return source == target or family is not None and view_compatible(family.view_type, source)


def body_may_raise(blocks: 'tuple[MIRBlock, ...] | list[MIRBlock]', slots: 'Mapping[MIRSlotId, MIRSlot]',
                   records: MIRRecords) -> bool:
    """`MIRFunction.exceptional_exits`: some statement may raise."""
    return any(statement_may_raise(stmt, slots, records) for block in blocks for stmt in block.statements)


def source_definition(stmt: MIRStatement) -> MIRSlotId | None:
    """The slot a statement defines whole: an assignment's unprojected
    target, or record storage it begins (a wrapper's construction leaves
    its source binding unassigned)."""
    match stmt:
        case MIRStorageInit(target=target, value=None):
            return target.root
        case MIRStorageInit() | MIRCallStmt() | MIRPrint():
            return None
        case MIRAssign(target=target):
            return None if target.projections else target.root
        case _:
            raise MIRValidationError("unknown instruction")


def _cyclic_blocks(blocks: dict[MIRBlockId, MIRBlock],
                   pred: dict[MIRBlockId, set[MIRBlockId]]) -> set[MIRBlockId]:
    """Two iterative DFS passes keep storage-site checks linear in CFG size."""
    visited: set[MIRBlockId] = set()
    order: list[MIRBlockId] = []
    for root in blocks:
        pending = [(root, False)]
        while pending:
            bid, finished = pending.pop()
            if finished:
                order.append(bid)
            elif bid not in visited:
                visited.add(bid)
                pending.append((bid, True))
                pending.extend((s, False) for s in successors(blocks[bid].terminator))
    visited.clear()
    cyclic: set[MIRBlockId] = set()
    for root in reversed(order):
        if root in visited:
            continue
        component: set[MIRBlockId] = set()
        pending_ids = [root]
        while pending_ids:
            bid = pending_ids.pop()
            if bid not in visited:
                visited.add(bid)
                component.add(bid)
                pending_ids.extend(pred[bid])
        if len(component) > 1 or root in pred[root]:
            cyclic.update(component)
    return cyclic


_UNINITIALIZED, _INITIALIZED, _MAYBE = "uninitialized", "initialized", "maybe"


class _Initialization:
    """Which places are initialized at each point: the one place-keyed
    analysis of storage initialization. Its places are each slot's whole
    storage, and each field of a destination record (a constructor's
    result), which a holder bound to it (`MIRSlot.bound_storage`, the
    constructor's `self`) reaches. A place is uninitialized, initialized,
    or maybe (on some path only): `must` holds the places initialized on
    every path, `may` those on some. A storage begin, a whole write of a
    slot and a write of a field initialize it; a move, and a call handed
    an argument's storage, leave that storage uninitialized; an edge
    leaving a region ends the storage it owns. A raise leaves the body:
    there is no exceptional edge to weigh."""

    def __init__(self, fn: MIRFunction, slots: 'Mapping[MIRSlotId, MIRSlot]',
                 blocks: 'Mapping[MIRBlockId, MIRBlock]', regions: MIRRegionFlow,
                 reachable: frozenset[MIRBlockId], records: MIRRecords) -> None:
        self.slots, self.blocks, self.records = slots, blocks, records
        self.bound = {s.id: s.bound_storage for s in fn.slots if s.bound_storage is not None}
        self.fields = {s.id: tuple(MIRPlace(s.id, (f,)) for f in records[s.type].fields)
                       for s in fn.slots if s.destination and s.type in records}
        entry = frozenset(MIRPlace(s.id) for s in fn.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL))
        universe = frozenset({MIRPlace(s.id) for s in fn.slots} | {p for fs in self.fields.values() for p in fs})
        incoming: dict[MIRBlockId, list[tuple[MIRBlockId, frozenset[MIRSlotId]]]] = {bid: [] for bid in reachable}
        for edge, transition in regions.edges.items():
            if edge.source in reachable and transition.target is not None:
                incoming[transition.target].append((edge.source, transition.ended))
        must_out = {bid: universe for bid in reachable}
        may_out: dict[MIRBlockId, frozenset[MIRPlace]] = {bid: frozenset() for bid in reachable}
        self.must_in: dict[MIRBlockId, frozenset[MIRPlace]] = {}
        self.may_in: dict[MIRBlockId, frozenset[MIRPlace]] = {}
        work = deque(reachable)
        queued = set(reachable)
        while work:
            bid = work.popleft()
            queued.remove(bid)
            musts = [self.ended(must_out[source], ended) for source, ended in incoming[bid]]
            mays = [self.ended(may_out[source], ended) for source, ended in incoming[bid]]
            if bid == fn.entry:
                musts.append(entry)
                mays.append(entry)
            must = frozenset.intersection(*musts) if musts else universe
            may = frozenset().union(*mays)
            self.must_in[bid], self.may_in[bid] = must, may
            for stmt in blocks[bid].statements:
                must, may = self.step(stmt, must, may)
            if must != must_out[bid] or may != may_out[bid]:
                must_out[bid], may_out[bid] = must, may
                for target in successors(blocks[bid].terminator):
                    if target not in queued:
                        work.append(target)
                        queued.add(target)
        self._states: dict[MIRBlockId, list[tuple[frozenset[MIRPlace], frozenset[MIRPlace]]]] = {}

    @staticmethod
    def ended(places: frozenset[MIRPlace], ended: frozenset[MIRSlotId]) -> frozenset[MIRPlace]:
        return frozenset(p for p in places if p.root not in ended) if ended else places

    def tracked(self, place: MIRPlace) -> tuple[MIRPlace, bool] | None:
        """The analysis's place `place` lies in -- a slot's storage, or a
        destination's field, through the holder bound to it -- and whether it
        is that place exactly; None for a place under any other holder."""
        root, projections = place.root, place.projections
        if root in self.bound and projections[:1] == (MIRDeref(),):
            root, projections = self.bound[root], projections[1:]
        if not projections:
            return MIRPlace(root), True
        if root in self.fields and isinstance(projections[0], MIRField):
            return MIRPlace(root, projections[:1]), len(projections) == 1
        return None

    def step(self, stmt: MIRStatement, must: frozenset[MIRPlace], may: frozenset[MIRPlace]
             ) -> tuple[frozenset[MIRPlace], frozenset[MIRPlace]]:
        target = statement_target(stmt)
        found = self.tracked(target) if target is not None else None
        cleared = frozenset(self.consumed(stmt))
        if cleared:
            must, may = must - cleared, may - cleared
        if found is not None and found[1]:
            initialized = self.initialized(stmt) if builds_base(stmt) else {found[0]}
            must, may = must | initialized, may | initialized
        return must, may

    def consumed(self, stmt: MIRStatement) -> list[MIRPlace]:
        """The storage a statement's operation consumes, once per consuming
        operand: a move's source; a call argument the callee binds as its
        own storage; a storage-owning slot a sum construction or an
        aggregate construction takes whole, which moves it."""
        value = stmt.call if isinstance(stmt, MIRCallStmt) else stmt.value if isinstance(stmt, MIRAssign) else None
        match value:
            case MIRMove(source=source):
                return [MIRPlace(source)]
            case MIRCall(summary=summary) if summary is not None:
                taken = [sid for sid, binding in zip(value.arguments, summary.parameters) if binding.storage]
            case MIRWrap(source=source) if source is not None and not source.projections:
                taken = [source.root]
            case MIRConstruct(fields=fields):
                taken = [sid for sid in fields if isinstance(sid, MIRSlotId)]
            case _:
                return []
        return [MIRPlace(sid) for sid in taken if self.slots[sid].shape.owns_storage]

    def initialized(self, stmt: MIRStatement) -> frozenset[MIRPlace]:
        """The fields of a destination a statement initializes
        (`initialized_places`: a base's constructor's), as this analysis
        tracks them; none for any other statement."""
        found = (self.tracked(place) for place in initialized_places(stmt, self.records))
        return frozenset(f[0] for f in found if f is not None and f[1] and f[0].projections)

    def states(self, bid: MIRBlockId) -> list[tuple[frozenset[MIRPlace], frozenset[MIRPlace]]]:
        """The state before each statement of a block, and before its terminator."""
        if bid not in self._states:
            must, may = self.must_in[bid], self.may_in[bid]
            found = [(must, may)]
            for stmt in self.blocks[bid].statements:
                must, may = self.step(stmt, must, may)
                found.append((must, may))
            self._states[bid] = found
        return self._states[bid]

    @staticmethod
    def state(place: MIRPlace, must: frozenset[MIRPlace], may: frozenset[MIRPlace]) -> str:
        return _INITIALIZED if place in must else _MAYBE if place in may else _UNINITIALIZED

    def field_write(self, bid: MIRBlockId, index: int, place: MIRPlace) -> str:
        """The state of the field a write of `place` writes, before it: a
        destination's field it writes exactly; any other place a write
        reaches is initialized storage the write replaces."""
        found = self.tracked(place)
        if found is None or not found[1] or not found[0].projections:
            return _INITIALIZED
        return self.state(found[0], *self.states(bid)[index])

    def check(self, reachable: frozenset[MIRBlockId]) -> None:
        """The rules: a read needs its place initialized -- a field it reads,
        or each field of a destination it reads whole; a use of a bound
        holder other than through a field reads its storage whole, and
        borrowing a destination whole needs only its begun storage. An
        initialization needs its storage uninitialized on every path; an
        in-place write of owned storage needs it initialized."""
        slots = self.slots

        def whole(root: MIRSlotId, must: frozenset[MIRPlace], what: str) -> None:
            if not all(p in must for p in self.fields.get(root, ())):
                raise MIRDefiniteAssignmentError(f"{what} before every field is initialized")

        def read(sid: MIRSlotId, place: MIRPlace | None, must: frozenset[MIRPlace], borrow: bool) -> None:
            if sid in self.bound:
                if place is not None and (found := self.tracked(place)) is not None and found[0].projections:
                    if found[0] not in must:
                        raise MIRDefiniteAssignmentError("read before field initialization")
                    return
                whole(self.bound[sid], must, "use of the record")
                return
            slot = slots[sid]
            if sid in self.fields and place is not None and place.projections:
                found = self.tracked(place)
                if found is not None and found[0] not in must:
                    raise MIRDefiniteAssignmentError("read before field initialization")
                return
            if sid in self.fields and not borrow:
                whole(sid, must, "use of the record")
            if MIRPlace(sid) in must:
                return
            _require(not slot.shape.inline_sum, "read before storage initialization")
            if slot.shape.owns_storage:
                raise MIRDefiniteAssignmentError("read after move")

        def initialize(stmt: MIRStatement, target: MIRPlace, must: frozenset[MIRPlace],
                       may: frozenset[MIRPlace]) -> None:
            slot = slots[target.root]
            fact = stmt.storage_write if isinstance(stmt, MIRAssign) else None
            # A record's whole write with no fact fills its storage too.
            initializes = isinstance(stmt, MIRStorageInit) or (
                isinstance(fact, MIRStorageWrite) and fact.mode is MIRStorageWriteMode.INITIALIZE) or (
                fact is None and slot.shape.is_storage)
            if initializes and target in may:
                if isinstance(slot.storage_duration, MIRRegionId):
                    raise MIRRepeatedInitializationError(stmt.loc)
                _require(not slot.shape.owned_tuple, "repeated tuple initialization")
                _require(not slot.shape.owns_storage, "repeated storage initialization")
            if fact == MIRStorageWrite(MIRStorageWriteMode.IN_PLACE) and target not in must:
                _require(not slot.shape.inline_sum, "assignment before storage initialization")
                _require(not slot.shape.leaf_storage, "replacement before storage initialization")

        for bid in sorted(reachable, key=lambda b: b.index):
            block = self.blocks[bid]
            states = self.states(bid)
            for index, stmt in enumerate(block.statements):
                must, may = states[index]
                target = statement_target(stmt)
                if target is not None and not target.projections:
                    initialize(stmt, target, must, may)
                value = stmt.value if isinstance(stmt, MIRAssign) else None
                for sid, place in _operand_places(stmt):
                    read(sid, place, must, isinstance(value, MIRBorrow) and place is not None
                         and not place.projections)
                consumed = self.consumed(stmt)
                if len(set(consumed)) != len(consumed):
                    raise MIRDefiniteAssignmentError("read after move")
                if builds_base(stmt):
                    # A base's fields are initialized in the record being built, never replaced.
                    _require(not self.initialized(stmt) & may, "repeated field initialization")
                elif target is not None and target.projections:
                    found = self.tracked(target)
                    if found is None or not found[0].projections:
                        read(target.root, None, must, False)
                    elif not found[1] and found[0] not in must:
                        raise MIRDefiniteAssignmentError("write under a field before its initialization")
            must, _ = states[-1]
            term = block.terminator
            if isinstance(term, MIRReturn) and term.value is not None:
                read(term.value, None, must, False)


def _operand_places(stmt: MIRStatement) -> tuple[tuple[MIRSlotId, MIRPlace | None], ...]:
    """Each slot a statement's value reads, with the place it reads when it
    reads one (a read, copy or borrow of a place); None for a whole slot
    (an alias, a move, a call's argument, a construct's operand)."""
    match stmt:
        case MIRCallStmt(call=call):
            return tuple((sid, None) for sid in call.arguments)
        case MIRPrint(arguments=arguments):
            return tuple((sid, None) for sid in arguments)
        case MIRStorageInit():
            return ()
    value = stmt.value
    match value:
        case (MIRRead(source=source) | MIRCopy(source=source) | MIRUnionExtract(source=source)
              | MIRBorrow(source=source) | MIRIsAlternative(source=source)):
            return ((source.root, source),)
        case MIRWrap(source=source) if source is not None:
            return ((source.root, source),)
    return tuple((sid, None) for sid in operands(value))


def _validate_structure(fn: MIRFunction) -> None:
    # Each summary's path walks, made once by its contract check and read by every call of it.
    # Over the body's layouts: whether a path's records verify is the summary's and the
    # lowering's check (the validator holds no definitions).
    summary_walks: IdentityMap[MIRCallSummary, MIRPathWalks] = IdentityMap()
    for summary in fn.call_summaries:
        summary_walks[summary] = walks = {}
        problem = summary_problem(summary, fn.record_layouts, walks)
        _require(problem is None, problem or "invalid call summary")
    _require(len({s.callee.identity for s in fn.call_summaries}) == len(fn.call_summaries),
             "duplicate call summary identity")
    call_summaries = IdentitySet(fn.call_summaries)
    _require(fn.kind in (MIRBodyKind.FREE_FUNCTION, MIRBodyKind.METHOD, MIRBodyKind.CONSTRUCTOR),
             "unsupported body kind")
    # A constructor, and only a constructor, builds a record where its caller receives it.
    _require((fn.kind is MIRBodyKind.CONSTRUCTOR) == any(s.destination for s in fn.slots),
             "constructor result mismatch")
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(result_problem(fn.return_type, fn.borrowed_result, fn.return_representation) is None,
             "unsupported return type or access")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    _require(all(isinstance(b, MIRParameterBinding) for b in fn.parameter_bindings), "invalid parameter binding")
    # The pairing reads each slot's kind; a count mismatch refuses there.
    _require(all(isinstance(s.kind, MIRSlotKind) for s in fn.slots), "invalid slot kind")
    bindings = fn.slot_bindings
    regions = _region_structure(fn)
    records = fn.record_layouts
    _require(len(records) == len(fn.records), "duplicate record layout")

    def defined(typ: TpyType) -> bool:
        """Whether the body supplies the definition storage of `typ` has: a
        record's or an owned leaf's layout, a container's registered
        definition (`MIRFunction.containers`)."""
        cls = value_class(typ)
        if cls is MIRClass.CONTAINER:
            return typ in records.containers
        return typ in records and records[typ].opaque == (cls is MIRClass.OWNED_LEAF)

    def facts(typ: TpyType) -> MIRAggregateFacts:
        """The copy and move facts of record or container storage of `typ`
        over the body's layouts (`aggregate_facts`)."""
        found = aggregate_facts(typ, records.get)
        _require(found is not None, "record place needs layout")
        return found
    # A record result's fields need its layout; a container's members come from its type.
    _require(fn.borrowed_result is None
             or value_class(fn.borrowed_result.type) in CONTAINER_CLASSES | {MIRClass.VIEW}
             or fn.borrowed_result.type in records, "missing borrowed return record layout")
    field_types: dict[MIRFieldId, TpyType] = {}

    def shape_at(place: MIRPlace) -> MIRShape:
        return place_shape(place, slots, records)

    for record in fn.records:
        _require(type(record.opaque) is bool, "invalid record layout kind")
        if record.opaque:
            # The builtin certificate: one opaque buffer, freely copied and moved.
            _require(value_class(record.type) is MIRClass.OWNED_LEAF and record.fields == ()
                     and record.copyable is True and record.movable is True, "invalid opaque leaf layout")
        else:
            _require(value_class(record.type) is MIRClass.RECORD, "unsupported record layout identity")
        _require(type(record.copyable) is bool and type(record.movable) is bool,
                 "invalid record eligibility")
        _require(isinstance(record.ancestors, tuple) and len(set(record.ancestors)) == len(record.ancestors)
                 and not (record.opaque and record.ancestors)
                 and all(isinstance(a, NominalType) and value_class(a) is MIRClass.RECORD and a != record.type
                         for a in record.ancestors), "invalid record layout ancestors")
        owners = (record.type, *record.ancestors)
        seen: set[MIRFieldId] = set()
        for member in record.fields:
            _require(isinstance(member, MIRField) and isinstance(member.id, MIRFieldId)
                     and member.id.owner in owners and bool(member.id.name)
                     and member.id not in seen and modeled_field(member.type),
                     "invalid record layout field")
            # An owned-leaf field is a place of that leaf's opaque storage,
            # a record field a place of its member record's.
            cls = value_class(member.type)
            bare = unwrap_readonly(member.type)
            _require(cls is not MIRClass.OWNED_LEAF or bare in records and records[bare].opaque,
                     "owned-leaf field needs its opaque layout")
            _require(cls is not MIRClass.RECORD or bare in records and not records[bare].opaque,
                     "record field needs its layout")
            # An Optional record field stores its payload record inline.
            _require(cls is not MIRClass.OPTIONAL or bare.inner in records and not records[bare.inner].opaque,
                     "optional record field needs its payload layout")
            seen.add(member.id)
            # A field is one storage of one type in every layout carrying it
            # (its declaring owner's and each descendant's).
            _require(field_types.setdefault(member.id, member.type) == member.type,
                     "inconsistent field type")
    global_ids: set[MIRGlobalId] = set()
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        shape = slot.shape
        _require(isinstance(shape, MIRShape) and isinstance(shape.cls, MIRClass)
                 and isinstance(shape.holding, MIRHolding) and type(shape.readonly) is bool
                 and isinstance(shape.members, tuple)
                 and all(m is None or isinstance(m, MIRShape) and type(m.readonly) is bool for m in shape.members),
                 "invalid slot shape")
        # Every rule below reads the class: it is the type's, checked once
        # here with the rest of the shape's intrinsic rules.
        representation = slot_representation(slot, bindings)
        problem = shape_problem(shape, slot.type, representation=representation, producer=slot.producer)
        caller = slot.kind is MIRSlotKind.PARAMETER and caller_storage(shape, representation)
        _require(problem is None, f"slot {problem}")
        if shape.owned_sum:
            _require(slot.kind is MIRSlotKind.LOCAL and not slot.readonly and slot.storage_duration is not None,
                     "inline record sum needs local storage")
            _require(all(m is None or not m.is_storage or m.cls is not MIRClass.RECORD or m.type in records
                         for m in shape.members), "invalid inline sum record")
        if slot.storage_duration is not None:
            _require(isinstance(slot.storage_duration, (MIRStorageDuration, MIRRegionId))
                     and (shape.owns_storage or slot.shape.inline_sum
                          or shape.cls is MIRClass.UNION or caller)
                     # A by-reference scalar wrapper parameter is the caller's storage.
                     and ((slot.storage_duration is MIRStorageDuration.CALLER and caller)
                          # A by-value owned leaf, an `Own[...]` container or record
                          # is the body's storage from entry to exit.
                          or (slot.storage_duration is MIRStorageDuration.BODY
                              and slot.kind is MIRSlotKind.PARAMETER and slot.shape.is_storage)
                          or ((slot.storage_duration is MIRStorageDuration.BODY
                               or isinstance(slot.storage_duration, MIRRegionId))
                              and slot.kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY))),
                     "invalid storage duration fact")
        if slot.kind is MIRSlotKind.GLOBAL:
            # An owned-leaf global is external static storage, read through a readonly handle.
            _require(isinstance(slot.global_id, MIRGlobalId) and bool(slot.global_id.module and slot.global_id.name)
                     and slot.global_id not in global_ids
                     and (shape.is_scalar or slot.shape.owned_leaf_borrow),
                     "invalid or duplicate global identity")
            global_ids.add(slot.global_id)
        else:
            _require(slot.global_id is None, "global identity on local slot")
        # A container's members are its type's (`shape_problem`); the
        # layouts this body supplies for them are checked here.
        _require(shape.cls not in CONTAINER_CLASSES
                 or not isinstance(admitted_members(slot.type, slot.readonly, records), str),
                 "unsupported native element")
        if shape.holding is MIRHolding.CURSOR:
            _require(slot.kind is MIRSlotKind.TEMPORARY, "iterator must be an internal temporary")
        elif shape.holding is MIRHolding.ALIAS:
            _require(isinstance(slot.alias_source, MIRPlace), "unsupported payload alias")
        elif shape.cls is MIRClass.TUPLE:
            if slot.shape.owned_tuple:
                _require(slot.kind is MIRSlotKind.LOCAL and slot.storage_duration is not None,
                         "owned tuple needs local backing placement")
            _require(all(not m.is_storage or m.type in records for m in shape.elements),
                     "invalid owned tuple record")
        elif shape.is_scalar:
            _require(not slot.readonly or slot.kind is MIRSlotKind.GLOBAL,
                     "unsupported slot type or form")
        elif shape.is_storage:
            # Owned storage is placed (mutable: `shape_problem`), of a type
            # whose definition the body supplies.
            _require(defined(slot.type) and (slot.kind is not MIRSlotKind.PARAMETER or slot.storage_duration is not None),
                     "unsupported record storage type or form")
            _require(slot.storage_duration is not None,
                     "owned storage needs a placement")
        elif shape.borrow_holder:
            _require(not slot.shape.owned_leaf_borrow or slot.type in records and records[slot.type].opaque,
                     "unsupported reference slot type or form")
        _require(shape.holding is MIRHolding.ALIAS or slot.alias_source is None,
                 "payload alias source on non-alias slot")
        _require(not slot.destination or (
            slot.kind is MIRSlotKind.TEMPORARY and shape.is_storage and shape.cls is MIRClass.RECORD
            and slot.storage_duration is MIRStorageDuration.BODY), "invalid destination storage")
        bound_to = slots.get(slot.bound_storage) if slot.bound_storage is not None else None
        _require(slot.bound_storage is None or (
            bound_to is not None and bound_to.destination and slot.kind is MIRSlotKind.LOCAL
            and shape.is_pointer and not shape.readonly and slot.type == bound_to.type), "invalid bound storage")
    # Each parameter slot holds its binding's value.
    for sid, binding in bindings.items():
        bound = replace(binding, value=slots[sid].shape)
        problem = parameter_contract_problem(bound)
        _require(problem is None, f"parameter {problem}")
        _require(bound == binding, "parameter slot differs from its binding")

    def member_operand(src: MIRSlotId | MIRConstant, member: MIRShape) -> bool | None:
        """Whether `src` fills a construct's member of shape `member`, and
        whether filling it may raise (None: it does not fill it). A holder
        member stores the loan its operand holds -- a view member a readonly
        holder's of its family, a tuple's pointer an equal holder's; an inert
        leaf takes a scalar's value; an owned leaf copies what a readonly
        holder lends (its own borrow, a `String` borrow or a view of its
        family) or takes owned temporary storage of its type; a record or
        container is copied through a holder of it or moved out of temporary
        storage of its type; an Optional record member takes the payload a
        borrowed Optional holder lends, if engaged."""
        if isinstance(src, MIRConstant):
            return None
        source = slots.get(src)
        if source is None:
            return None
        if member.is_holder:
            ok = (source.shape.leaf_borrow and view_compatible(member.type, source.type)
                  if member.cls is MIRClass.VIEW else member_compatible(source.shape, member))
            return False if ok else None
        match member.cls:
            case MIRClass.LEAF:
                return False if source.shape.is_scalar and source.type == member.type else None
            case MIRClass.OWNED_LEAF:
                if source.shape.leaf_borrow and copies_into(source.type, member.type):
                    return bool(type_def_of(member.type).copy_may_raise)
                return False if (source.shape.leaf_storage and source.type == member.type
                                 and source.kind is MIRSlotKind.TEMPORARY) else None
            case MIRClass.OPTIONAL:
                # The definition decided copy or move; an empty payload copies nothing.
                payload = member.payload.type
                return (record_copy_may_raise(payload) if source.shape.record_payload
                        and source.shape.payload.type == payload and records.get(payload) is not None else None)
            case cls if cls in AGGREGATE_CLASSES:
                found = aggregate_facts(member.type, records.get) if defined(member.type) else None
                if found is None or source.type != member.type:
                    return None
                if source.shape.is_pointer and found.copyable:
                    return found.copy_may_raise
                return False if (source.shape.is_storage and source.kind is MIRSlotKind.TEMPORARY
                                 and not source.readonly and found.movable) else None
        return None

    def construct_ok(value: MIRConstruct, target: MIRShape) -> bool:
        """One operand per member of the target's shape (`member_operand`):
        a tuple's elements (`construct_members`), or a container literal's
        elements (a dict's keys and values alternating), whose length the
        literal fixes and which allocates. It may raise exactly when a
        member's copy may, or it is a literal. A record is built by its
        constructor's call, never field by field."""
        if (not isinstance(value.fields, tuple) or type(value.may_raise) is not bool
                or target.cls is MIRClass.RECORD):
            return False
        literal = target.cls is MIRClass.CONTAINER
        if literal:
            cycle = target.members
            # Literal construction: the one place the compiler knows a container's length.
            if (not cycle or len(value.fields) % len(cycle)
                    or is_array(target.type) and len(value.fields) != target.type.type_args[1]):
                return False
            members = [cycle[i % len(cycle)] for i in range(len(value.fields))]
        else:
            members = [m for _, m in construct_members(target, records)]
            if len(value.fields) != len(members):
                return False
        raises = literal
        for src, member in zip(value.fields, members):
            copied = member_operand(src, member)
            if copied is None:
                return False
            raises = raises or copied
        return value.may_raise is raises

    def record_copy_may_raise(typ: TpyType) -> bool:
        return facts(typ).copy_may_raise

    def validate_wrapper_copy(value: MIRCopy, target: MIRShape) -> None:
        """A sum or a tuple copied into a value of `target`: the same class
        and selection, member by member by what the target member holds --
        a holder takes the source member's loan, or lends an inline one at
        the access of the place it lies in; storage takes a copyable copy,
        whatever access the source has; a scalar the same value."""
        source = place_info(value.source)
        # A union copies between equal layouts; a tuple only from a whole slot.
        _require(source.cls is target.cls and (target.cls is not MIRClass.UNION or source.type == target.type)
                 and (target.cls is not MIRClass.TUPLE or not value.source.projections), "wrapper copy source mismatch")
        _require(not (source.owned_tuple or target.owned_tuple), "owning tuple copy is unsupported")
        mismatch = "wrapper copy member type or access mismatch"
        _require(len(source.members) == len(target.members), mismatch)
        for src, dst in zip(source.members, target.members):
            if src is None or dst is None:
                _require(src is dst, mismatch)
            elif dst.is_storage:
                layout = records.get(dst.type)
                _require(dst.cls is MIRClass.RECORD and src.type == dst.type and src.cls is MIRClass.RECORD
                         and layout is not None and layout.copyable, "wrapper copy storage member mismatch")
            else:
                _require(member_binds(src, dst), mismatch)
        _require(value.may_raise is (copy_may_raise(target, facts) or reaches_element(value.source)),
                 "wrapper copy exit fact mismatch")

    def record_copy_ok(value: MIRCopy, typ: TpyType) -> bool:
        """A copy of record or container storage of type `typ`: the owned
        storage a holder points at, the body's own storage, or an inline
        member; it may raise exactly when copying it may (or an element
        access on the way)."""
        source = place_info(value.source)
        projections = value.source.projections
        return (facts(typ).copyable and source.type == typ and source.owned_place
                and (projections in ((), object_prefix(slots[value.source.root].shape))
                     or isinstance(projections[-1], MIRField))
                and value.may_raise is (record_copy_may_raise(typ) or reaches_element(value.source)))

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    def place_info(place: MIRPlace, *, write: bool = False, initialization: bool = False) -> MIRShape:
        _require(isinstance(place, MIRPlace), "invalid place")
        slot_type(place.root)
        slot = slots[place.root]
        _require(not (write and slot.kind is MIRSlotKind.GLOBAL and slot.readonly), "store through readonly global")
        shape = slot.shape
        tuple_member = False
        optional_member = False
        walked = walk(shape, place.projections, records)
        for hop in walked.hops:
            projection, shape = hop.projection, hop.reached
            _require(shape is not None, REGION_END)
            match projection:
                case MIRUnionPayload():
                    _require(not shape.is_storage or shape.type in records or shape.cls is MIRClass.CONTAINER,
                             "optional payload needs its layout")
                    optional_member = True
                case MIRTupleIndex():
                    tuple_member = True
                case MIRDeref():
                    tuple_member = False
                    optional_member = False
                case MIRField():
                    tuple_member = False
                    _require(shape.cls is not MIRClass.CONTAINER or bool(shape.members), "unsupported container field")
                    _require(field_types.setdefault(projection.id, projection.type) == projection.type,
                             "inconsistent field type")
        problem = store_problem(walked, initialization=initialization) if write else None
        _require(problem is None, problem)
        _require(not (write and tuple_member), "tuple element replacement is forbidden")
        _require(not (write and optional_member), "optional payload replacement is forbidden")
        # An aggregate is replaced whole only in place through its holder
        # (`IN_PLACE`) or as a member (`validate_member_write`); a view
        # member is rebound only by a view member write
        # (`validate_view_member_write`): every write of such a place reaches one.
        if shape.is_storage and not place.projections:
            _require(defined(shape.type), "record place needs layout")
        return shape

    def place_type(place: MIRPlace, *, write: bool = False) -> TpyType:
        return place_info(place, write=write).type

    def payload_operand(place: MIRPlace) -> MIRShape:
        """The payload a wrapper construction stores: a holder's whole value."""
        _require(isinstance(place, MIRPlace) and not place.projections, "wrapper payload operand must be a holder")
        slot_type(place.root)
        return slots[place.root].shape

    for slot in fn.slots:
        if slot.shape.holding is MIRHolding.ALIAS:
            source = slot.alias_source
            _require(sole_payload(source.projections)
                     and place_type(source) == slot.type, "invalid payload alias source")

    def validate_call(call: MIRCall) -> None:
        _require(isinstance(call, MIRCall) and call.summary in call_summaries,
                 "call summary does not belong to this body")
        summary = call.summary
        # No declaration can say a stub cannot raise yet.
        _require(not isinstance(summary.callee, THIRStubCallee) or call.may_raise is True,
                 "stub call must be a possible exceptional exit")
        _require(type(call.may_raise) is bool and call.may_raise is (not summary.normal_return_only),
                 "call exit fact mismatch")
        _require(len(call.arguments) == len(summary.parameters), "call arity mismatch")
        def storage_of(parameter: int) -> NominalType | None:
            argument = slots.get(call.arguments[parameter])
            return argument.type if argument is not None and record_type(argument.type) else None

        def unbound(parameter: int, storage: NominalType | None) -> bool:
            # The argument check below refuses the binding itself.
            ref = summary.parameters[parameter].lent_record
            return storage is not None and ref is not None and not binds_at(records, storage, ref.type)
        # Each record a published path reads fields of has its layout here.
        _require(all(record in records for record in crossed_records(summary, summary_walks[summary], storage_of,
                                                                     unbound)), "call path record has no layout")
        # A pure stub reads every argument whatever its declared access.
        pure = isinstance(summary.callee, THIRStubCallee) and summary.callee.contract is THIRStubContract.PURE
        for sid, binding in zip(call.arguments, summary.parameters):
            _require(sid in slots, "unknown slot ID")
            source = slots[sid]
            _require(source.kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            if binding.protocol:
                # Only a builtin leaf's dispatch is the stub's own runtime code;
                # the leaf is read in place (a view: the leaf it views) or by
                # value. A native container is read whole by its own runtime code.
                bound = type_def_of(read_leaf(source))
                _require(source.shape.container_or_view or bound is not None and (
                    bound.owned_leaf and source.shape.leaf_borrow or bound.loan_inert and source.shape.is_scalar),
                         "call protocol argument is not a builtin leaf")
                continue
            cls = binding.cls
            if cls is MIRClass.CONTAINER_VIEW:
                # A view by value: the loan it holds.
                _require(source.type == binding.type and source.shape.container_view_holder
                         and (not source.readonly or binding.readonly), "call container argument mismatch")
                continue
            if cls in AGGREGATE_CLASSES and binding.storage:
                # An aggregate handed over by value (an `Own[...]` parameter,
                # an element a container takes): the temporary the call moves from.
                _require(source.type == binding.type and source.shape.is_storage
                         and source.kind is MIRSlotKind.TEMPORARY and defined(binding.type),
                         "call record argument mismatch")
                continue
            if cls is MIRClass.OWNED_LEAF and not binding.storage and source.shape.view_holder:
                # A view of the parameter's family is lent as the view it already is.
                _require(view_compatible(source.type, binding.type), "call owned-leaf argument mismatch")
                continue
            if cls in WRAPPER_CLASSES:
                # A wrapper holder of the objects the binding borrows, or of its scalars.
                _require(source.type == binding.type and wrapper_compatible(source.shape, binding.value),
                         "call wrapper argument mismatch")
                continue
            # Record storage binds at its own type or a struct-base ancestor's;
            # every other binding is exact.
            _require(source.type == binding.type or cls is MIRClass.RECORD
                     and binds_at(records, source.type, binding.type), "call argument type mismatch")
            if cls is MIRClass.VIEW:
                _require(source.shape.view_holder and binding.readonly, "call view argument mismatch")
            elif cls in AGGREGATE_CLASSES:
                # A record or container lent through a holder of it; a pure
                # stub reads its receiver whatever access it declares.
                _require(source.shape.borrowed_aggregate and source.shape.cls is cls
                         and (not source.readonly or binding.readonly or pure), "call record argument mismatch")
            elif cls is MIRClass.OWNED_LEAF:
                # Lent for the call at a borrowing passing, else the callee's own copy.
                _require(source.type == binding.type and (
                    source.shape.leaf_storage and source.kind is MIRSlotKind.TEMPORARY if binding.storage
                    else source.shape.owned_leaf_borrow), "call owned-leaf argument mismatch")
            else:
                _require(source.type == binding.type and source.shape.is_scalar,
                         "call scalar argument mismatch")

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    owning_blocks: set[MIRBlockId] = set()
    reachable = reachable_blocks(fn)
    initialization = _Initialization(fn, slots, blocks, regions, reachable, records)

    def scoped_initialization(target: MIRSlot, block: MIRBlock) -> bool:
        """The one INITIALIZE placement rule of storage of every kind (an
        owned leaf, an owned tuple, a scalar wrapper's payload, a record):
        scoped storage is initialized in the region that owns it, or inside
        a full expression nested in it (its result built in the
        expression's own region); False for storage of no region, whose
        placement its caller checks. Initializing it again within one
        activation is `_Initialization`'s to refuse."""
        if not isinstance(target.storage_duration, MIRRegionId):
            return False
        _require(target.storage_duration in regions.chains[block.region], "scoped initialization needs owning region")
        return True

    def validate_owned_write(stmt: MIRAssign, block: MIRBlock, target: MIRSlot) -> None:
        fact, value = stmt.storage_write, stmt.value
        _require(isinstance(fact, MIRStorageWrite) and fact.rebind_owner is None
                 and fact.mode in (MIRStorageWriteMode.INITIALIZE, MIRStorageWriteMode.IN_PLACE),
                 "owned leaf write needs an initialization or replacement fact")
        if fact.mode is MIRStorageWriteMode.INITIALIZE and not scoped_initialization(target, block):
            _require(target.storage_duration is MIRStorageDuration.BODY, "backing write needs private body storage")
            owning_blocks.add(block.id)
        validate_owned_value(value, target.type, MIRPlace(target.id))

    def validate_call_record(call: MIRCall, typ: TpyType) -> None:
        """Record storage of type `typ` a call initializes or replaces: fresh
        storage the callee hands over by value (`Own[R]`), never a borrowed
        result's referent copied. The loans it stores are the summary's
        transfers into the result. A call whose result is a struct-base
        ancestor's (`MIRCall.initializes`, the one fact the callee's
        result type states again) initializes that base's fields of it."""
        validate_call(call)
        base = call.initializes
        _require(base is None or (base in records.get(typ, MIRRecordLayout(typ, (), False, False)).ancestors
                                  and base in records),
                 "call base initialization type mismatch")
        _require(call.summary.callee.signature.hands_over(typ if base is None else base),
                 "call record result type mismatch")

    def validate_element_write(stmt: MIRAssign, typ: TpyType) -> None:
        """An element replaced in place (`xs[i] = v`): a weak update of the
        region, under the container's root. The value takes the member's
        form: an inert leaf read by value, an owned leaf as owned storage
        takes one, a record constructed, copied through a holder or moved
        out of a temporary."""
        fact, value = stmt.storage_write, stmt.value
        _require(isinstance(fact, MIRStorageWrite) and fact.mode is MIRStorageWriteMode.IN_PLACE
                 and fact.rebind_owner == stmt.target.root, "element write needs an in-place fact")
        for operand in operands(value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        cls = place_info(stmt.target).cls
        if cls is MIRClass.LEAF:
            match value:
                case MIRRead(source=source):
                    _require(not source.projections and slots[source.root].shape.is_scalar
                             and slots[source.root].type == typ and value.may_raise is False,
                             "element write type mismatch")
                case MIRConstant():
                    _require(leaf_constant(typ, value.value), "constant type or range mismatch")
                case _:
                    raise MIRValidationError("element write type mismatch")
        elif cls is MIRClass.OWNED_LEAF:
            validate_owned_value(value, typ, stmt.target)
        else:
            _require(value_class(typ) in AGGREGATE_CLASSES and defined(typ), "element write type mismatch")
            _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove, MIRCall)), "element write type mismatch")
            validate_record_value(value, typ, stmt.target)

    def validate_record_value(value: MIRRvalue, typ: TpyType, target: MIRPlace) -> None:
        """What record or container storage of type `typ` takes, wherever
        it lives (a local's or a temporary's own storage, what a holder
        points at, an element, an inline member): a construct of its
        members (a container's literal of the members the target has), a
        copy of an aggregate source (`record_copy_ok`), a move out of other
        owned storage of its type, or a call handing over a fresh result."""
        for operand in operands(value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        match value:
            case MIRConstruct():
                _require(construct_ok(value, place_info(target)),
                         "incomplete or mistyped container literal" if value_class(typ) is MIRClass.CONTAINER
                         else "incomplete or mistyped record construction")
            case MIRCopy():
                _require(record_copy_ok(value, typ), "record copy source or eligibility")
            case MIRMove(source=source):
                _require(source in slots and slots[source].shape.is_storage
                         and slots[source].type == typ and not slots[source].readonly
                         and facts(typ).movable and source != target.root,
                         "record move source or eligibility")
            case MIRCall():
                validate_call_record(value, typ)
            case _:
                raise MIRValidationError("record write needs a construct, move, copy or call")

    def field_write(stmt: MIRStatement, initializing: bool, *, owned: bool) -> MIRShape:
        """The one target check of a field write, whatever the member's
        class: the written field's state (`_Initialization.field_write`)
        decides. A write of an uninitialized field is its initialization --
        storage the field owns is INITIALIZEd, and the field's own
        `readonly` allows it; any other replaces the field -- IN_PLACE, never
        of a declared `readonly[...]` field or one reached readonly. A
        readonly holder on the way refuses either. A view member or a scalar
        owns no storage: its write carries no storage fact."""
        _require(isinstance(stmt, MIRAssign), "field write needs a storage write fact")
        fact = stmt.storage_write
        if owned:
            mode = MIRStorageWriteMode.INITIALIZE if initializing else MIRStorageWriteMode.IN_PLACE
            _require(isinstance(fact, MIRStorageWrite) and fact.mode is mode and fact.rebind_owner is None,
                     "field write needs its initialization or replacement fact")
        else:
            _require(fact is None, "field write of no storage needs no storage fact")
        target = place_info(stmt.target, write=True, initialization=initializing)
        _require(initializing or not target.readonly, "store through readonly storage")
        return target

    def validate_member_write(stmt: MIRStatement, typ: TpyType, initializing: bool) -> None:
        """An inline record or container member written whole (`ln.a = v`,
        `self.items = data`) under the record a holder reaches, which keeps
        its identity (`field_write`). A replacement is C++'s copy- or
        move-assignment, so its member is movable; an initialization
        constructs in place."""
        target = field_write(stmt, initializing, owned=True)
        _require(target.owned_place, "store through readonly storage")
        _require(defined(typ) and (initializing or facts(typ).movable),
                 "record member replacement needs movable record")
        for operand in operands(stmt.value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        validate_record_value(stmt.value, typ, stmt.target)

    # The temporaries a sum construction moved into a payload: each once.
    wrapped_payloads: set[MIRSlotId] = set()

    def validate_optional_field_write(stmt: MIRStatement, initializing: bool) -> None:
        """`h.p = v` on an Optional record field, or a whole write of an
        owned sum slot: the whole value written (`field_write`; the record
        around a field keeps its identity). The value empties it, moves a
        record built in temporary storage into its payload, or copies
        another Optional's payload."""
        target = field_write(stmt, initializing, owned=True)
        _require(target.cls is MIRClass.OPTIONAL, "store through readonly storage")
        payload = target.payload.type
        container = target.payload.cls is MIRClass.CONTAINER
        layout = records.get(payload)
        # A container payload (a slot's only) is defined by its container layout, moved whole.
        _require(container or layout is not None and not layout.opaque, "optional record field needs its payload layout")
        for operand in operands(stmt.value):
            _require(operand in slots and slots[operand].kind is not MIRSlotKind.GLOBAL,
                     "global value needs explicit read")
        match stmt.value:
            case MIRWrap(alternative=0, source=None):
                pass
            case MIRWrap(alternative=1, source=source) if source is not None:
                operand = payload_operand(source)
                _require(operand.type == payload and operand.is_storage and not operand.readonly
                         and slots[source.root].kind is MIRSlotKind.TEMPORARY
                         and defined(payload) and (container or layout.movable)
                         and source.root not in wrapped_payloads,
                         "optional field payload needs a moved temporary")
                wrapped_payloads.add(source.root)
            case MIRCopy():
                validate_wrapper_copy(stmt.value, target)
            case _:
                raise MIRValidationError("optional field write needs an optional construction or copy")

    def validate_view_member_write(stmt: MIRAssign, typ: TpyType, initializing: bool) -> None:
        """`t.s = v`: a view member bound to the loan a view holder holds,
        under a record a mutable path reaches (`field_write`). It replaces
        no storage, so it carries no storage event."""
        target = field_write(stmt, initializing, owned=False)
        _require(target.is_pointer, "store through readonly storage")
        source = slots.get(stmt.value.source) if isinstance(stmt.value, MIRAlias) else None
        _require(source is not None and source.kind is not MIRSlotKind.GLOBAL and source.shape.view_holder
                 and view_compatible(typ, source.type), "view member write needs a view holder")

    def validate_owned_field_write(stmt: MIRAssign, typ: TpyType, initializing: bool) -> None:
        """An owned-leaf field's buffer initialized or replaced in place
        (`field_write`), under the record a borrowed holder reaches."""
        field_write(stmt, initializing, owned=True)
        validate_owned_value(stmt.value, typ, stmt.target)

    def validate_owned_value(value: MIRRvalue, typ: TpyType, target: MIRPlace) -> None:
        for operand in operands(value):
            _require(operand in slots and (slots[operand].kind is not MIRSlotKind.GLOBAL
                                           or isinstance(value, MIRCopy) and slots[operand].shape.owned_leaf_borrow),
                     "global value needs explicit read")
        match value:
            case MIRCopy():
                source = place_info(value.source)
                # The source is a whole owned buffer: the body's storage, a field, or
                # what a holder (a view holder included) points at.
                _require(copies_into(source.type, typ) and source.owned_place
                         and (owned_leaf_place_type(value.source, slots, records) is not None
                              or value.source.projections
                              == object_prefix(slots[value.source.root].shape)),
                         "owned leaf copy source mismatch")
                td = type_def_of(typ)
                # An element's copy also checks its index.
                _require(value.may_raise is (bool(td.copy_may_raise) or reaches_element(value.source)),
                         "owned leaf copy exit fact mismatch")
            case MIRMove():
                source = slots[value.source]
                _require(source.shape.leaf_storage and source.type == typ and MIRPlace(value.source) != target,
                         "owned leaf move source mismatch")
            case MIROp():
                td = type_def_of(typ)
                _require(isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                         and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                         and all(slots[o].shape.is_scalar or slots[o].shape.leaf_borrow for o in value.operands),
                         "primitive operation needs leaf operands and result")
                _require(td.primitive_ops and all(primitive_operand(slots[o]) for o in value.operands),
                         "primitive operation lacks the primitive contract")
                # No fact says a primitive operation cannot raise yet.
                _require(value.may_raise is True, "primitive operation must be a possible exceptional exit")
            case MIRConstant():
                _require(owned_constant(typ, value.value), "constant type or range mismatch")
            case MIRCall():
                # A callee returns an owned leaf by value: fresh storage the caller
                # owns, unless the result may borrow an argument (a holder, then a copy).
                validate_call(value)
                _require(value.summary.borrowed_result is None
                         and owned_value_type(signature_result(value.summary.callee.signature)) == typ,
                         "call owned result type mismatch")
            case _:
                raise MIRValidationError("owned leaf write needs a copy, move, operation, constant or call")
    payload_initializations: set[MIRSlotId] = set()
    payload_init_blocks: set[MIRBlockId] = set()
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for index, stmt in enumerate(block.statements):
            _require(isinstance(stmt, (MIRAssign, MIRStorageInit, MIRCallStmt, MIRPrint)),
                     "unknown instruction")
            target_place = statement_target(stmt)
            if isinstance(stmt, MIRPrint):
                _require(isinstance(stmt.arguments, tuple) and all(
                    sid in slots and (slots[sid].shape.is_scalar or slots[sid].shape.leaf_borrow)
                    and slots[sid].kind is not MIRSlotKind.GLOBAL
                    for sid in stmt.arguments), "print needs local inert leaf arguments")
                # A user method could format any other leaf (an enum's `__str__`).
                _require(all(primitive_operand(slots[sid]) for sid in stmt.arguments),
                         "print argument lacks the primitive contract")
            if isinstance(stmt, MIRCallStmt):
                validate_call(stmt.call)
                _require(isinstance(signature_result(stmt.call.summary.callee.signature), VoidType),
                         "effect-only call needs void result")
            if fn.regions:
                active = regions.chains[block.region]
                targets = () if target_place is None else (target_place.root,)
                for sid in (*statement_reads(stmt), *targets):
                    _require(sid in slots, "unknown slot ID")
                    residence = slots[sid].residence
                    _require(residence is None or residence in active, "use outside binding residence")
            if target_place is None:
                continue
            # A write of a field not yet initialized is its initialization.
            initializing = (block.id in reachable
                            and initialization.field_write(block.id, index, target_place) == _UNINITIALIZED)
            _require(block.id not in reachable
                     or initialization.field_write(block.id, index, target_place) != _MAYBE,
                     "write of a field initialized on some path only")
            target_shape = place_info(target_place, write=True, initialization=initializing)
            target_type = target_shape.type
            target = slots[target_place.root]
            if isinstance(stmt, MIRStorageInit) and stmt.value is None:
                # Record storage begun at entry, its fields initialized by the body.
                _require(not stmt.target.projections and target.destination and stmt.alternative is None
                         and block.id == fn.entry, "storage begin needs destination storage at entry")
                continue
            if isinstance(stmt, MIRStorageInit):
                match stmt:
                    case MIRStorageInit():
                        _require(not stmt.target.projections and target.kind is MIRSlotKind.LOCAL
                                 and target.shape.inline_sum,
                                 "physical initialization needs local inline sum")
                        _require(type(stmt.alternative) is int and stmt.alternative == 0
                                 and isinstance(stmt.value, MIRConstant), "invalid wrapper default")
                        layout = shape_at(stmt.target)
                        default_type = (None if layout.cls is not MIRClass.UNION
                                        or layout.alternatives[0] is None else layout.alternatives[0].type)
                        expected = None if default_type is None else zero_value_of(default_type)
                        _require((default_type is None or expected is not None)
                                 and type(stmt.value.value) is type(expected) and stmt.value.value == expected,
                                 "invalid wrapper default value")
                _require(target.id not in payload_initializations, "repeated payload initialization")
                payload_initializations.add(target.id)
                if isinstance(target.storage_duration, MIRRegionId):
                    _require(target.storage_duration == block.region, "scoped initialization needs owning region")
                else:
                    _require(target.storage_duration is MIRStorageDuration.BODY
                             and (not fn.regions or block.region == blocks[fn.entry].region),
                             "physical initialization needs owning placement")
                    payload_init_blocks.add(block.id)
                continue
            value = stmt.value
            fact = stmt.storage_write
            if not stmt.target.projections and target.shape.leaf_storage:
                # Every write to owned storage carries its storage event, whatever produces the value.
                validate_owned_write(stmt, block, target)
                continue
            if view_member(stmt.target) is not None:
                validate_view_member_write(stmt, target_type, initializing)
                continue
            if elements_place(stmt.target):
                validate_element_write(stmt, target_type)
                continue
            if stmt.target.projections and owned_leaf_place_type(stmt.target, slots, records) is not None:
                validate_owned_field_write(stmt, target_type, initializing)
                continue
            if (stmt.target.projections and isinstance(member := stmt.target.projections[-1], MIRField)
                    and value_class(member.type) is MIRClass.OPTIONAL) or (
                    not stmt.target.projections and target.shape.owned_sum):
                validate_optional_field_write(stmt, initializing)
                continue
            if (stmt.target.projections and isinstance(member := stmt.target.projections[-1], MIRField)
                    and value_class(member.type) in AGGREGATE_CLASSES):
                validate_member_write(stmt, target_type, initializing)
                continue
            # The write's checks follow the storage it lands in: an owned
            # tuple's, a scalar wrapper's inline payload, or an aggregate's.
            if fact is None:
                pass
            elif not (isinstance(fact, MIRStorageWrite) and isinstance(fact.mode, MIRStorageWriteMode)):
                raise MIRValidationError("invalid storage write fact")
            elif target.shape.owned_tuple and not stmt.target.projections:
                _require(fact.mode is MIRStorageWriteMode.INITIALIZE and fact.rebind_owner is None
                         and isinstance(value, MIRConstruct), "tuple initialization needs owning tuple construction")
                if not scoped_initialization(target, block):
                    _require(target.storage_duration is MIRStorageDuration.BODY,
                             "tuple initialization needs owning placement")
                    owning_blocks.add(block.id)
            elif target.shape.scalar_wrapper and not stmt.target.projections:
                # A temporary (a call's Optional argument) is only ever initialized.
                _require(fact.mode in (MIRStorageWriteMode.INITIALIZE, MIRStorageWriteMode.IN_PLACE)
                         and fact.rebind_owner is None
                         and (target.kind is MIRSlotKind.LOCAL or (
                             target.kind is MIRSlotKind.TEMPORARY and fact.mode is MIRStorageWriteMode.INITIALIZE))
                         and isinstance(value, (MIRWrap, MIRCopy)),
                         "payload write needs a local or temporary scalar wrapper operation")
                if fact.mode is MIRStorageWriteMode.INITIALIZE:
                    _require(target.id not in payload_initializations, "repeated payload initialization")
                    payload_initializations.add(target.id)
                    if not scoped_initialization(target, block):
                        payload_init_blocks.add(block.id)
            else:
                _require(isinstance(value, (MIRConstruct, MIRCopy, MIRMove, MIRCall)),
                         "record write on non-record operation")
                match fact.mode:
                    case MIRStorageWriteMode.INITIALIZE if isinstance(target.storage_duration, MIRRegionId):
                        # Initialized in the owning region, or inside the full
                        # expression nested in it that builds its arguments.
                        _require(not stmt.target.projections and target.shape.is_storage
                                 and fact.rebind_owner is None,
                                 "scoped initialization needs owning region")
                        scoped_initialization(target, block)
                    case MIRStorageWriteMode.INITIALIZE if builds_base(stmt):
                        # A base's fields of the record being built, through the holder bound to it.
                        _require(target.bound_storage is not None and stmt.target.projections == (MIRDeref(),)
                                 and fact.rebind_owner is None, "base initialization needs the record built")
                    case MIRStorageWriteMode.INITIALIZE:
                        _require(not stmt.target.projections and target.shape.is_storage
                                 and target.storage_duration is MIRStorageDuration.BODY and fact.rebind_owner is None,
                                 "backing write needs private body storage")
                    case MIRStorageWriteMode.IN_PLACE:
                        # An aggregate rebuilt in place: the one its holder lends.
                        _require(fact.rebind_owner == stmt.target.root
                                 and (target.shape.borrowed_aggregate or target.shape.record_payload)
                                 and stmt.target.projections == sole_object(target.shape),
                                 "invalid in-place rebind owner or target")
            if (target.shape.owned_tuple or target.shape.inline_sum) and not stmt.target.projections:
                # What a whole write of inline members replaces or ends is
                # weighed from its fact (`reach.storage_events`).
                _require(fact is not None, "inline aggregate whole write needs its storage write fact")
            if not stmt.target.projections:
                _require(target.shape.holding is not MIRHolding.ALIAS or isinstance(value, MIRUnionExtract),
                         "payload alias requires extraction")
            for operand in operands(value):
                slot_type(operand)
                _require(slots[operand].kind is not MIRSlotKind.GLOBAL or isinstance(value, MIRRead)
                         or isinstance(value, (MIRBorrow, MIRCopy)) and slots[operand].shape.owned_leaf_borrow,
                         "global value needs explicit read")
            match value:
                case MIRRangeAdvance():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.id == source.id
                             and target.type == INT32 and target.shape.is_scalar
                             and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.step) is int and value.step in (-1, 1), "invalid range induction")
                case MIRIteratorInit():
                    source = slots[value.source]
                    _require(not stmt.target.projections and target.shape.holding is MIRHolding.CURSOR
                             and source.shape.container_or_view
                             and target.type == source.type
                             and target.shape.members == with_access(source.shape.members, target.readonly)
                             and (not source.readonly or target.readonly), "iterator source or access mismatch")
                case MIRIteratorHasNext() | MIRIteratorRead() | MIRIteratorAdvance():
                    source = slots[value.source]
                    _require(source.shape.holding is MIRHolding.CURSOR
                             and not stmt.target.projections, "iterator operation needs iterator source and local target")
                    member = source.shape.element
                    match value:
                        case MIRIteratorHasNext():
                            _require(target_type == BOOL and target.shape.is_scalar,
                                     "iterator test needs bool target")
                        case MIRIteratorAdvance():
                            _require(target.id == source.id, "advance must update its iterator")
                        case MIRIteratorRead() if member.cls is MIRClass.OWNED_LEAF:
                            # An owned-leaf element is read through a borrow of it or a view.
                            _require(target.kind is not MIRSlotKind.PARAMETER and target.shape.leaf_borrow
                                     and (target.type == member.type or view_compatible(target.type, member.type)),
                                     "iterator element type or access mismatch")
                        case MIRIteratorRead():
                            # An inert leaf is read by value, a record element through a holder.
                            _require(target.type == member.type
                                     and (target.shape.is_scalar if member.cls is MIRClass.LEAF
                                          else target.shape.is_pointer)
                                     and target.kind is not MIRSlotKind.PARAMETER
                                     and (member.cls is MIRClass.LEAF
                                          or not (member.readonly or source.readonly) or target.readonly),
                                     "iterator element type or access mismatch")
                case MIRWrap():
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.shape.cls in SUM_CLASSES
                             and target.kind is not MIRSlotKind.PARAMETER, "wrap needs local sum destination")
                    target_layout = target.shape
                    _require(type(value.alternative) is int
                             and 0 <= value.alternative < len(target_layout.members),
                             "invalid wrap alternative")
                    member = target_layout.alternatives[value.alternative]
                    _require((member is None) == (value.source is None), "wrap payload mismatch")
                    if member is not None:
                        _require(member_compatible(payload_operand(value.source), member),
                                 "wrap payload type or access mismatch")
                case MIRCopy() if (shape_at(stmt.target).cls in WRAPPER_CLASSES
                                   or place_info(value.source).cls in WRAPPER_CLASSES):
                    # A sum's or a tuple's copy, by the value it copies; a
                    # record copied through an Optional holder is a record copy.
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and shape_at(stmt.target).cls in WRAPPER_CLASSES
                             and target.kind is not MIRSlotKind.PARAMETER,
                             "wrapper copy needs local wrapper destination")
                    validate_wrapper_copy(value, target.shape)
                case MIRIsAlternative():
                    source_layout = place_info(value.source)
                    if source_layout.cls is MIRClass.OPTIONAL:
                        _require(target_type == BOOL and value.alternatives == (1,),
                                 "presence test needs optional source and bool destination")
                    _require(not stmt.target.projections and target.shape.is_scalar
                             and target_type == BOOL and source_layout.cls in SUM_CLASSES,
                             "union test type mismatch")
                    _require(bool(value.alternatives) and len(set(value.alternatives)) == len(value.alternatives)
                             and all(type(i) is int and 0 <= i < len(source_layout.members)
                                     for i in value.alternatives), "invalid tested union alternatives")
                case MIRUnionExtract():
                    source = value.source
                    _require(not stmt.target.projections and sole_payload(source.projections)
                             and target.kind is not MIRSlotKind.PARAMETER, "unsupported union extraction")
                    _require(place_type(source) == target_type, "union extraction type mismatch")
                    wrapper = shape_at(MIRPlace(source.root, source.projections[:-1]))
                    member = wrapper.alternatives[source.projections[-1].alternative]
                    if member.cls is MIRClass.LEAF:
                        _require(target.shape.holding is MIRHolding.ALIAS and target.alias_source == source,
                                 "scalar extraction must bind payload storage")
                    else:
                        _require(target.shape.is_pointer
                                 and (not member.readonly or target.readonly), "union extraction increases access")
                case MIRConstruct() if shape_at(stmt.target).cls is MIRClass.TUPLE:
                    # A tuple built from one operand per element: it holds no
                    # storage of its own beyond the members (`construct_ok`).
                    target = slots[stmt.target.root]
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER,
                             "tuple operation needs tuple destination")
                    _require(construct_ok(value, target.shape), "tuple payload type or access mismatch")
                case MIRConstruct() | MIRCopy() | MIRMove() | MIRCall() if (
                        not isinstance(value, MIRCall) or isinstance(fact, MIRStorageWrite)):
                    if fact is None or (fact.mode is MIRStorageWriteMode.INITIALIZE and not isinstance(
                            slots[stmt.target.root].storage_duration, MIRRegionId)):
                        owning_blocks.add(block.id)
                    target = slots[stmt.target.root]
                    _require(value_class(target_type) in AGGREGATE_CLASSES and defined(target_type) and (
                        (not stmt.target.projections and target.shape.is_storage)
                        or stmt.target.projections
                        and stmt.target.projections == sole_object(target.shape)),
                        "record destination type")
                    if builds_base(stmt):
                        # A base's fields initialized in the record a holder reaches: built in place.
                        _require(fact is not None and fact.mode is MIRStorageWriteMode.INITIALIZE
                                 and fact.rebind_owner is None, "base initialization needs an initialization fact")
                    elif stmt.target.projections:
                        _require(facts(target_type).movable
                                 and fact is not None and fact.mode is MIRStorageWriteMode.IN_PLACE,
                                 "unsupported record replacement")
                    validate_record_value(value, target_type, stmt.target)
                case MIRBorrow():
                    target = slots[stmt.target.root]
                    source = place_info(value.source)
                    source_type, readonly = source.type, source.readonly
                    element = elements_place(value.source)
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER,
                             "storage borrow type mismatch")
                    if target.shape.container_view_holder:
                        # A view of the whole region (an unstepped slice): it
                        # never raises, and it views the region's member type.
                        _require(element and not value.may_raise
                                 and target.shape.element.type == source_type,
                                 "container view borrow type mismatch")
                    elif source.borrow_holder:
                        # A view member read: the holder takes the loan stored there.
                        _require(target.shape.view_holder and bool(value.source.projections)
                                 and isinstance(value.source.projections[-1], MIRField)
                                 and source.cls is MIRClass.VIEW and view_compatible(target_type, source_type),
                                 "storage borrow type mismatch")
                    else:
                        _require(target.shape.is_pointer
                                 and source.owned_place
                                 and (target_type == source_type
                                      or target.shape.view_holder and view_compatible(target_type, source_type)),
                                 "storage borrow type mismatch")
                    _require(not readonly or target.readonly, "borrow increases access")
                case MIRCall():
                    validate_call(value)
                    # A follows-receiver result has the access of the receiver bound at this call.
                    result = (value.summary.result_at(slots[value.arguments[0]].readonly) if value.arguments
                              else value.summary.borrowed_result)
                    _require(not stmt.target.projections, "call needs whole result holder")
                    if result is None:
                        # An `Own[T]` scalar result is a plain T.
                        _require(target.shape.is_scalar
                                 and target_type == unwrap_own(signature_result(value.summary.callee.signature)),
                                 "call result type or target mismatch")
                    else:
                        _require(target.shape.borrow_holder
                                 and target_type == result.type and (not result.readonly or target.readonly),
                                 "call result type or access mismatch")
                case MIRConstant() if target.shape.leaf_borrow:
                    # A str or bytes literal lives in static storage its holder borrows.
                    _require(not stmt.target.projections and target.kind is not MIRSlotKind.PARAMETER
                             and type(value.value) in (str, bytes) and owned_constant(read_leaf(target), value.value),
                             "static literal needs a borrowed holder of its type")
                case MIRConstant():
                    _require(leaf_constant(target_type, value.value), "constant type or range mismatch")
                case MIRRead():
                    _require(target.shape.holding is not MIRHolding.ALIAS and target_shape.cls is MIRClass.LEAF
                             and target_type == place_type(value.source), "read type mismatch")
                    _require(type(value.may_raise) is bool and (reaches_element(value.source) or not value.may_raise),
                             "only an element access may raise")
                case MIRAlias():
                    target = slots[stmt.target.root]
                    source = slots[value.source]
                    _require(not stmt.target.projections
                             and target.shape.borrow_holder
                             and source.shape.holding is target.shape.holding
                             and (target.shape.is_pointer or source.shape.cls is target.shape.cls)
                             and (not target.shape.members
                                  or target.shape.members == with_access(source.shape.members, target.readonly))
                             and (target_type == source.type
                                  or target.shape.view_holder and source.shape.leaf_borrow
                                  and view_compatible(target_type, source.type)), "alias type mismatch")
                    _require(target.kind is not MIRSlotKind.PARAMETER, "reference parameter reseat")
                    _require(not source.readonly or target.readonly, "alias increases access")
                case MIRCompare():
                    _require(value.op in COMPARISON_OPS,
                             "unsupported comparison")
                    _require(target_type == BOOL, "comparison result is not bool")
                    left, right = slots[value.left], slots[value.right]
                    certified = certified_primitive_comparison(left.type, right.type)
                    _require(all(s.shape.is_scalar or s.shape.leaf_borrow for s in (left, right))
                             and (left.type == right.type or certified),
                             "comparison operand type mismatch")
                    _require(certified and primitive_operand(left) and primitive_operand(right),
                             "comparison lacks the primitive contract")
                case MIRNot():
                    _require(target_type == BOOL and slot_type(value.operand) == BOOL
                             and slots[value.operand].shape.is_scalar,
                             "not operand or result is not bool")
                case MIROp():
                    _require(not stmt.target.projections and target.shape.is_scalar
                             and target.kind is not MIRSlotKind.GLOBAL
                             and isinstance(value.op, str) and bool(value.op) and type(value.may_raise) is bool
                             and isinstance(value.operands, tuple) and 1 <= len(value.operands) <= 2
                             and all(slots[o].shape.is_scalar or slots[o].shape.leaf_borrow for o in value.operands),
                             "primitive operation needs inert leaf operands and result")
                    _require(primitive_leaf(target_type) and all(primitive_operand(slots[o]) for o in value.operands),
                             "primitive operation lacks the primitive contract")
                    _require(value.may_raise is True, "primitive operation must be a possible exceptional exit")
                case _:
                    raise MIRValidationError("unknown rvalue semantics")
        term = block.terminator
        if fn.regions:
            used = (term.condition,) if isinstance(term, MIRBranch) else (
                (term.value,) if isinstance(term, MIRReturn) and term.value is not None else ())
            for sid in used:
                _require(sid in slots, "unknown slot ID")
                _require(slots[sid].residence is None or slots[sid].residence in regions.chains[block.region],
                         "use outside binding residence")
        for successor in successors(term):
            _require(successor in blocks, "invalid block target")
            pred[successor].add(block.id)
        match term:
            case MIRBranch():
                _require(slot_type(term.condition) == BOOL and slots[term.condition].shape.is_scalar,
                         "branch condition is not bool")
                _require(slots[term.condition].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")
            case MIRReturn():
                if term.value is None:
                    _require(isinstance(fn.return_type, VoidType), "missing return value")
                elif fn.borrowed_result is not None:
                    source = slots[term.value]
                    _require(source.shape.borrow_holder
                             and source.type == fn.borrowed_result.type
                             and (not source.readonly or fn.borrowed_result.readonly),
                             "borrowed return type or access mismatch")
                elif slots[term.value].shape.leaf_storage:
                    # An owned leaf returns by value: its storage is copied or moved out.
                    _require(slot_type(term.value) == owned_value_type(fn.return_type), "return type mismatch")
                elif owned_aggregate_result(fn.return_type) is not None:
                    # The body's own aggregate storage (a local's, or an
                    # `Own[...]` parameter's) is moved out: a readonly or
                    # non-movable one would be copied instead.
                    source = slots[term.value]
                    # Destination storage is built where the caller receives it: nothing moves.
                    _require(source.shape.owns_storage and (source.kind is not MIRSlotKind.PARAMETER
                                                          or handed_storage(source, fn.slot_bindings))
                             and source.type == owned_aggregate_result(fn.return_type) and not source.readonly
                             and defined(source.type) and (source.destination or facts(source.type).movable),
                             "owned record return needs movable owned storage")
                else:
                    _require(slot_type(term.value) == fn.return_type and slots[term.value].shape.is_scalar,
                             "return type mismatch")
                    _require(slots[term.value].kind is not MIRSlotKind.GLOBAL, "global value needs explicit read")

    exceptional = body_may_raise(fn.blocks, slots, records)
    _require(type(fn.exceptional_exits) is bool and fn.exceptional_exits is exceptional,
             "exceptional exit fact mismatch")
    cyclic = _cyclic_blocks(blocks, pred)
    _require(not owning_blocks.intersection(cyclic), "owning operation in cycle")
    _require(not payload_init_blocks.intersection(cyclic), "payload initialization in cycle")

    parameters = {s.id for s in fn.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
    writes = {bid: {sid for s in blocks[bid].statements if (sid := source_definition(s)) is not None}
              for bid in reachable}
    # Intersection is a must analysis; initialize at top, with a synthetic
    # parameter-only incoming edge at entry even if the entry has a back edge.
    incoming = {bid: set(slots) for bid in reachable}
    outgoing = {bid: set(slots) for bid in reachable}
    work = deque(reachable)
    queued = set(reachable)
    incoming_edges = {bid: [] for bid in reachable}
    for edge, transition in regions.edges.items():
        if edge.source in reachable and transition.target is not None:
            incoming_edges[transition.target].append((edge.source, transition.reset))
    while work:
        bid = work.popleft()
        queued.remove(bid)
        sources = [outgoing[source] - reset for source, reset in incoming_edges[bid]]
        if bid == fn.entry:
            sources.append(parameters)
        new_in = set.intersection(*sources)
        new_out = new_in | writes[bid]
        incoming[bid] = new_in
        if new_out != outgoing[bid]:
            outgoing[bid] = new_out
            for target in successors(blocks[bid].terminator):
                if target not in queued:
                    work.append(target)
                    queued.add(target)
    for bid in reachable:
        assigned = incoming[bid].copy()
        block = blocks[bid]
        for stmt in block.statements:
            reads = set(statement_reads(stmt))
            if not reads <= assigned:
                raise MIRDefiniteAssignmentError("read before definite assignment")
            if (definition := source_definition(stmt)) is not None:
                assigned.add(definition)
        term = block.terminator
        match term:
            case MIRBranch():
                if term.condition not in assigned:
                    raise MIRDefiniteAssignmentError("branch before definite assignment")
            case MIRReturn() if term.value is not None:
                if term.value not in assigned:
                    raise MIRDefiniteAssignmentError("return before definite assignment")
    # Storage initialization, after the slot-level assignment above.
    initialization.check(reachable)
