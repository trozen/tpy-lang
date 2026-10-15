"""MIR's emit pass: builds a body's CFG by folding the lowering plan its
check pass returned (`plan.py`). It reads no THIR and classifies nothing:
every arm matches a plan class, and what it builds depends only on that
plan and on emit state (blocks, regions, storage durations, raise flags,
reachability)."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..typesys import BOOL, INT32, TpyType
from ..identity_map import IdentityMap
from .coverage import PAYLOAD_RECORD, MIRUnsupported
from .definitions import (
    MIRComposedConstruct, MIRFieldInitializer, layout_copy_may_raise,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRCallStmt, MIRCompare,
    MIRClass, MIRConstant, MIRConstruct, MIRContainerElements, MIRCopy, MIRDeref, MIRFunction, MIRGlobalId, MIRGoto,
    MIRHolding, MIRIsAlternative, MIRIsPresent, MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit,
    MIRIteratorRead, MIRMemberInit, MIRMemberInits, MIRMove, MIRNot, MIROp, MIROptionalConstruct, MIROptionalCopy,
    MIROptionalPayload, MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRPoint, MIRPrint, MIRRangeAdvance, MIRRead,
    MIRReceiverInit, MIRRecordLayout, MIRRecordStorageInit, MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode, MIRRegion,
    MIRRegionId, MIRReturn, MIRRvalue, MIRShape, MIRSlot, MIRSlotId, MIRSlotKind, MIRStatement, MIRStorageDuration,
    MIRStorageInit, MIRTerminator, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex, MIRTupleInitialization,
    MIRUnionConstruct, MIRUnionCopy, MIRUnionExtract, MIRUnionPayload, member_init_operands, statement_target,
)
from .plan import (
    PAbsent, PAnchored, PAppend, PAssign, PBody, PBorrowBinding, PBorrowedBinding, PBorrowedCall, PBorrowedField,
    PBorrowedName, PBorrowedSelect, PCall, PCallStatement, PCallValue, PCompare, PConditional, PConstant, PConstruct,
    PContainer, PContainerAlias, PContainerCall, PContainerField, PContainerLiteral, PContainerMove,
    PConvert, PDeclare, PElementPlace, PElementStore, PElementValue,
    PExpressionStatement, PFieldPlace, PForEach, PFullExpression, PGlobalPlace, PHoist, PIf, PIsAlternative,
    PIsPresent, PJump, PLentTemporary, PLogical, PMemberArgument, PMemberComposed, PMemberConstant, PMemberOptional,
    PMembers, PMemberView, PMoveOut, PNamePlace, PNarrowAlias, PNot, POperation, POptionalAssign, POptionalDeclare,
    POptionalFieldCopy, POptionalFieldValue, POptionalOperand, POptionalRecordAssign, POwnedArgument,
    POwnedCall, POwnedField, POwnedFormConvert, POwnedLiteral, POwnedPassthrough, POwnedPlace, POwnedRecordDeclare,
    POwnedReturn, PParamCopy, PPrint, PPromote, PRangeLoop, PRead, PRecordCall, PRecordCopy, PRecordMove,
    PRecordSource, PRecordTemporary, PReplacement, PReturnBorrowed, PReturnHolder,
    PReturnValue, PScalarAssign, PSetItemCall, PSpanAlias, PSpanRegion, PSpanSlice, PStatementTemporaries, PTemporary,
    PTemporaryRecordPlace, PTupleAlias, POwnedTupleDeclare, PTupleElementPlace, PTupleLiteral, PTupleName,
    PUnionAssign, PUnionDeclare, PUnionPayloadPlace, PViewCall, PViewField, PViewMemberAssign, PViewName,
    PViewOf, PViewSlice, PWalrus, PWhile, PWrapperCapture, PWrapperCopy, PWrapperLiteral, PWrapperValue,
)
from .shapes import place_shape
from .validate import body_may_raise, reaches_element, statement_reads, successors, validate_function

_VIEW_PLANS = (PViewName, PViewField, PViewSlice, PViewCall, PViewOf)
_OWNED_PLANS = (POwnedPlace, POwnedLiteral, POwnedCall, POwnedPassthrough, POwnedFormConvert, POwnedField)
_RECORD_VALUES = (PConstruct, PRecordCall, PRecordMove, PRecordCopy)
_SPAN_PLANS = (PSpanAlias, PSpanRegion, PSpanSlice)

class MIRFallthroughError(Exception):
    """A non-void body's end is reachable; the lowering refuses it at the
    body it checked."""


def _unknown(plan: object) -> TypeError:
    return TypeError(f"no lowering for plan node {type(plan).__name__}")


def _view(plan: object) -> bool:
    """A plan whose value an operation reads through a view holder."""
    return isinstance(plan, _VIEW_PLANS) or isinstance(plan, PElementValue) and plan.view


def _owned(plan: object) -> bool:
    """A plan whose value is an owned leaf an operation reads through a borrowed holder."""
    return (isinstance(plan, _OWNED_PLANS) or isinstance(plan, (PConvert, POperation)) and plan.owned
            or isinstance(plan, PElementValue) and not plan.view)


def _scalar(typ: TpyType) -> MIRShape:
    """An inert leaf held by value: an expression's scalar result."""
    return MIRShape(typ, MIRClass.LEAF)


def _owned_storage(typ: TpyType) -> MIRShape:
    """Owned-leaf storage held in place."""
    return MIRShape(typ, MIRClass.OWNED_LEAF)


def _owned_holder(typ: TpyType) -> MIRShape:
    """A readonly holder of owned-leaf storage."""
    return MIRShape(typ, MIRClass.OWNED_LEAF, MIRHolding.POINTER, True)


def _view_holder(typ: TpyType) -> MIRShape:
    """A readonly holder typed by a view: what it views is owned-leaf storage."""
    return MIRShape(typ, MIRClass.VIEW, MIRHolding.POINTER, True)


def _copy_may_raise(layouts: Mapping[TpyType, MIRRecordLayout], typ: TpyType) -> bool:
    """Whether copying a record of `typ` the body models may raise
    (`layout_copy_may_raise` over the body's layouts)."""
    return layout_copy_may_raise(layouts[typ], layouts.get)


class _Slots(Mapping[MIRSlotId, MIRSlot]):
    """An emitter's slots by identity: its list is indexed by slot index."""

    def __init__(self, slots: list[MIRSlot]) -> None:
        self.slots = slots

    def __getitem__(self, sid: MIRSlotId) -> MIRSlot:
        return self.slots[sid.index]

    def __iter__(self) -> Iterator[MIRSlotId]:
        return (slot.id for slot in self.slots)

    def __len__(self) -> int:
        return len(self.slots)


@dataclass
class _Block:
    id: MIRBlockId
    region: MIRRegionId
    statements: list[MIRStatement] = field(default_factory=list)
    terminator: MIRTerminator | None = None


class PlanEmitter:
    def __init__(self, body: MIRBodyId, plan: PBody) -> None:
        self.body = body
        self.plan = plan
        self.layouts = plan.layouts
        self.slots: list[MIRSlot] = []
        self.constant_bools: dict[MIRSlotId, bool] = {}
        # Optional arguments built empty for their call (`MIRCall.absent`).
        self.empty_arguments: set[MIRSlotId] = set()
        self.bindings: dict[str, MIRSlotId] = {}
        self.storage: dict[str, MIRSlotId] = {}
        self.globals: dict[MIRGlobalId, MIRSlotId] = {}
        self.blocks: list[_Block] = []
        self.region = MIRRegionId(body, 0)
        self.regions = [MIRRegion(self.region, None, MIRBlockId(body, 0))]
        self.current: _Block | None = self.block()
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []
        self.temp_scope = 0
        # The region each temporary plan scope opened in.
        self.temp_regions: dict[int, MIRRegionId] = {0: self.region}
        self.temp_storage: dict[int, MIRSlotId] = {}
        self.temp_holders: dict[int, MIRSlotId] = {}
        # By the THIR keys the plans carry (`PTemporary.key`, a statement's `operation`).
        self.backing_places: IdentityMap[object, MIRPlace] = IdentityMap()
        self.borrow_operations: IdentityMap[object, tuple[MIRPoint, ...]] = IdentityMap()

    # --- slots, blocks, writes ---------------------------------------------

    def block(self) -> _Block:
        block = _Block(MIRBlockId(self.body, len(self.blocks)), self.region)
        self.blocks.append(block)
        return block

    def slot(self, shape: MIRShape, kind: MIRSlotKind = MIRSlotKind.TEMPORARY, name: str | None = None, *,
             alias_source: MIRPlace | None = None, global_id: MIRGlobalId | None = None,
             storage_duration: MIRStorageDuration | MIRRegionId | None = None,
             record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT,
             passing: object = None) -> MIRSlotId:
        sid = MIRSlotId(self.body, len(self.slots))
        self.slots.append(MIRSlot(sid, shape, kind, name, alias_source=alias_source, global_id=global_id,
                                  storage_duration=storage_duration,
                                  record_storage=record_storage, passing=passing,
                                  residence=(self.regions[0].id if storage_duration is MIRStorageDuration.BODY else
                                             storage_duration if shape.is_storage
                                             and isinstance(storage_duration, MIRRegionId)
                                             else self.region) if kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
                                  else None))
        return sid

    def scoped_duration(self) -> MIRRegionId | MIRStorageDuration:
        """The storage of the current scope: its region, or the body's at top level."""
        return self.region if self.region.index else MIRStorageDuration.BODY

    def placed(self, placement: bool | None) -> MIRRegionId | MIRStorageDuration | None:
        """The storage duration a statement's placement gives (`plan.py`)."""
        if placement is None:
            return None
        return MIRStorageDuration.BODY if placement else self.scoped_duration()

    def declared_storage(self, plan: PUnionDeclare | POptionalDeclare | POwnedRecordDeclare
                         ) -> MIRRegionId | MIRStorageDuration | None:
        """The storage duration of what a declaration owns; a declaration
        THIR placed no storage for is refused here, where the CFG reaches it."""
        if plan.missing_placement is not None:
            raise MIRUnsupported(plan.missing_placement, "missing direct storage placement")
        return self.placed(plan.placement)

    def write(self, dest: MIRSlotId | MIRPlace, value: MIRRvalue, loc: object,
              storage_write: MIRRecordWrite | MIRPayloadWrite | MIRTupleInitialization | None = None) -> None:
        assert self.current is not None
        target = MIRPlace(dest) if isinstance(dest, MIRSlotId) else dest
        self.current.statements.append(MIRAssign(target, value, loc, storage_write))

    def borrow_operation(self, operation: object) -> None:
        assert self.current is not None
        point = MIRPoint(self.current.id, len(self.current.statements))
        self.borrow_operations[operation] = (*self.borrow_operations.get(operation, ()), point)

    def payload_write(self, inline: bool, mode: MIRPayloadWriteMode) -> MIRPayloadWrite | None:
        """The payload write of a wrapper holding its payload inline (a
        declaration's `shape.scalar_wrapper`, an assignment's `inline_payload`)."""
        if mode is MIRPayloadWriteMode.INITIALIZE and self.region.index != 0:
            mode = MIRPayloadWriteMode.INITIALIZE_REGION
        return MIRPayloadWrite(mode) if inline else None

    def end(self, term: MIRTerminator) -> None:
        assert self.current is not None and self.current.terminator is None
        self.current.terminator = term
        self.current = None

    def result(self, typ: TpyType, value: MIRRvalue, loc: object) -> MIRSlotId:
        dest = self.slot(_scalar(typ))
        self.write(dest, value, loc)
        # Only these expression temporaries have one definition; locals and select results can change.
        if typ == BOOL:
            match value:
                case MIRConstant(value=constant) if type(constant) is bool:
                    self.constant_bools[dest] = constant
                case MIRNot(operand=operand) if operand in self.constant_bools:
                    self.constant_bools[dest] = not self.constant_bools[operand]
        return dest

    def branch(self, cond: MIRSlotId, yes: MIRBlockId, no: MIRBlockId, loc: object) -> None:
        if cond in self.constant_bools:
            self.end(MIRGoto(yes if self.constant_bools[cond] else no, loc))
        else:
            self.end(MIRBranch(cond, yes, no, loc))

    def select(self, cond: MIRSlotId, then: object, otherwise: object, typ: TpyType, loc: object) -> MIRSlotId:
        dest = self.slot(_scalar(typ))
        yes, no, join = self.block(), self.block(), self.block()
        self.branch(cond, yes.id, no.id, loc)
        for block, arm in ((yes, then), (no, otherwise)):
            self.current = block
            value = arm if isinstance(arm, MIRSlotId) else self.value(arm)
            self.write(dest, MIRRead(MIRPlace(value)), loc)
            self.end(MIRGoto(join.id, loc))
        self.current = join
        return dest

    def holder(self, shape: MIRShape, value: MIRRvalue, loc: object) -> MIRSlotId:
        """A holder of a value already evaluated."""
        holder = self.slot(shape)
        self.write(holder, value, loc)
        return holder

    def fill(self, shape: MIRShape, evaluate: Callable[[], MIRRvalue], loc: object) -> MIRSlotId:
        """A holder allocated before the value it takes is evaluated."""
        holder = self.slot(shape)
        self.write(holder, evaluate(), loc)
        return holder

    def storage_steps(self, place: MIRPlace) -> tuple[MIRDeref, ...]:
        """The dereference from a place to the storage its value points at:
        none for storage held in place."""
        shape = place_shape(place, _Slots(self.slots), self.layouts)
        return (MIRDeref(),) if shape.is_pointer else ()

    # --- temporaries --------------------------------------------------------

    def declare_temporaries(self, planned: PStatementTemporaries | None) -> None:
        """The storage and holders of the temporaries a statement declares in
        the current temporary scope, then those it initializes."""
        if planned is None:
            return
        for temporary in planned.declared:
            if temporary.scope != self.temp_scope:
                continue
            assert self.temp_regions[temporary.scope] == self.region
            storage = self.slot(temporary.storage, MIRSlotKind.LOCAL, storage_duration=self.scoped_duration(),
                                record_storage=(MIRRecordStorageKind.OPTIONAL if temporary.optional
                                                else MIRRecordStorageKind.DIRECT))
            self.temp_storage[temporary.index] = storage
            self.backing_places[temporary.key] = MIRPlace(storage)
            self.temp_holders[temporary.index] = self.slot(temporary.holder)
            if temporary.optional:
                self.current.statements.append(MIRRecordStorageInit(MIRPlace(storage), temporary.loc))
        self.initialize(planned.initialized)

    def initialize(self, temporaries: tuple[PTemporary, ...]) -> None:
        """Build placed temporaries where the temporary plan initializes them."""
        for temporary in temporaries:
            storage = self.temp_storage[temporary.index]
            mode = MIRRecordWriteMode.OPTIONAL_ASSIGN if temporary.optional else self.initial_mode()
            self.write(storage, self.record_value(temporary.value), temporary.loc, MIRRecordWrite(mode))
            self.write(self.temp_holders[temporary.index], MIRBorrow(MIRPlace(storage)), temporary.loc)

    def initial_mode(self) -> MIRRecordWriteMode:
        return (MIRRecordWriteMode.INITIALIZE_ONCE if self.region.index == 0
                else MIRRecordWriteMode.INITIALIZE_REGION)

    def owned_initialization(self, dest: MIRSlotId) -> MIRRecordWrite:
        duration = self.slots[dest.index].storage_duration
        return MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION if isinstance(duration, MIRRegionId)
                              else MIRRecordWriteMode.INITIALIZE_ONCE)

    # --- places -------------------------------------------------------------

    def place(self, plan: object) -> MIRPlace:
        match plan:
            case PGlobalPlace():
                return MIRPlace(self.globals[plan.identity])
            case PNamePlace(payload=True):
                return MIRPlace(self.bindings[plan.name], (MIROptionalPayload(),))
            case PNamePlace():
                return MIRPlace(self.bindings[plan.name])
            case PUnionPayloadPlace():
                return MIRPlace(self.bindings[plan.name], (MIRUnionPayload(plan.alternative),))
            case PElementPlace():
                return self.element_place(plan)
            case PTupleElementPlace():
                root = self.bindings[plan.root] if isinstance(plan.root, str) else self.tuple_value(plan.root)
                return MIRPlace(root, (MIRTupleIndex(plan.index),))
            case PFieldPlace():
                base = self.place(plan.receiver)
                deref = self.storage_steps(base)
                payload = (MIROptionalPayload(),) if plan.payload else ()
                return MIRPlace(base.root, base.projections + deref + (plan.field,) + payload)
            case PTemporaryRecordPlace():
                storage = self.slot(plan.storage, storage_duration=self.region)
                self.backing_places[plan.key] = MIRPlace(storage)
                self.write(storage, self.record_value(plan.value), plan.loc,
                           MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                return MIRPlace(storage)
        raise _unknown(plan)

    def container_root(self, plan: PContainer) -> MIRPlace:
        """The place of a container: a binding's slot (owned storage, a
        borrowed container or a Span holder, each reaching its region
        directly), a container field of a record, or a call's result held."""
        match plan.root:
            case PNamePlace():
                return MIRPlace(self.bindings[plan.root.name])
            case PContainerField():
                return self.place(plan.root.place)
        return MIRPlace(self.container_slot(plan))

    def container_slot(self, plan: PContainer) -> MIRSlotId:
        """A slot holding a container place, for an operation that takes a
        holder (a call, an iterator): a binding itself, a borrowed container
        holder of a field, or the holder a call's result fills."""
        root = plan.root
        match root:
            case PNamePlace():
                return self.bindings[root.name]
            case PContainerCall():
                return self.fill(plan.holder, lambda: self.call(root.call), root.loc)
            case PContainerField():
                return self.fill(plan.holder, lambda: MIRBorrow(self.place(root.place)), root.loc)
        raise _unknown(root)

    def element_place(self, plan: PElementPlace) -> MIRPlace:
        """Evaluate a subscript's index or key, then name the container's
        element region: any element, never one by identity."""
        if plan.key:
            # An owned-leaf key is read by the lookup (its hash and its
            # comparisons), so its holder stays live through the access.
            self.result(BOOL, MIROp("key", (self.operand(plan.index),), may_raise=True), plan.index.loc)
        else:
            self.value(plan.index)
        root = self.container_root(plan.container)
        return MIRPlace(root.root, (*root.projections, MIRContainerElements()))

    def chain_raises(self, plan: object) -> bool:
        """Whether reading a place may raise: it reaches through an element
        subscript whose index may be out of range."""
        while isinstance(plan, PFieldPlace):
            plan = plan.receiver
        return isinstance(plan, PElementPlace) and plan.raises

    # --- values -------------------------------------------------------------

    def value(self, plan: object) -> MIRSlotId:
        """The slot a scalar value is evaluated into."""
        if isinstance(plan, PAnchored):
            self.initialize(plan.temporaries)
            return self.value(plan.body)
        loc = plan.loc
        match plan:
            case PCallValue():
                return self.result(plan.type, self.call(plan.call), loc)
            case PConstant():
                return self.result(plan.type, MIRConstant(plan.value), loc)
            case PConvert():
                return self.result(plan.type, self.conversion(plan), loc)
            case PRead():
                # A read through an element subscript may raise when its index is unproven.
                return self.result(plan.type, MIRRead(self.place(plan.place), self.chain_raises(plan.place)), loc)
            case POperation():
                return self.result(plan.type, self.operation(plan), loc)
            case PIsAlternative():
                found = self.result(BOOL, MIRIsAlternative(MIRPlace(self.bindings[plan.name]), plan.alternatives), loc)
                return self.result(BOOL, MIRNot(found), loc) if plan.negate else found
            case PWalrus():
                value = self.value(plan.value)
                self.write(self.place(plan.target), MIRRead(MIRPlace(value)), loc)
                return value
            case PNot():
                return self.result(BOOL, MIRNot(self.value(plan.operand)), loc)
            case PIsPresent():
                present = self.result(BOOL, MIRIsPresent(self.place(plan.place)), loc)
                return present if plan.negate else self.result(BOOL, MIRNot(present), loc)
            case PLogical():
                left = self.value(plan.left)
                return self.select(left, plan.right if plan.op == "&&" else left,
                                   left if plan.op == "&&" else plan.right, BOOL, loc)
            case PCompare():
                left = self.operator_operand(plan.left)
                return self.result(BOOL, MIRCompare(plan.op, left, self.operator_operand(plan.right)), loc)
            case PConditional():
                return self.select(self.value(plan.cond), plan.then, plan.orelse, plan.type, loc)
        raise _unknown(plan)

    def conversion(self, plan: PConvert | PPromote) -> MIROp:
        return MIROp(f"coerce:{plan.name}", (self.operand(plan.operand),), may_raise=True)

    def operation(self, plan: POperation) -> MIROp:
        # A primitive operation's contract admits raising.
        return MIROp(plan.op, tuple(self.operator_operand(o) for o in plan.operands), may_raise=True)

    def operator_operand(self, plan: object) -> MIRSlotId:
        """The slot an operator reads for one operand; a promoted operand is
        first converted, like a coercion, into the operator's type."""
        if not isinstance(plan, PPromote):
            return self.operand(plan)
        value = self.conversion(plan)
        if not plan.owned:
            return self.result(plan.type, value, plan.loc)
        temporary = self.slot(_owned_storage(plan.type), storage_duration=self.region)
        self.write(temporary, value, plan.loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return self.holder(_owned_holder(plan.type), MIRBorrow(MIRPlace(temporary)), plan.loc)

    def operand(self, plan: object) -> MIRSlotId:
        """The slot an operation reads: a scalar value, or a borrowed holder
        of an owned leaf or a view that stays live until the operation runs."""
        if _view(plan):
            return self.view_slot(plan)
        if _owned(plan):
            return self.owned_operand(plan)
        return self.value(plan)

    def view_slot(self, plan: object) -> MIRSlotId:
        """A view holder of a value: a view binding itself, else a new
        holder typed by the view (a source family's, for an owned source),
        written from `view_rvalue`."""
        if isinstance(plan, PViewName):
            return self.bindings[plan.name]
        view = plan.view if isinstance(plan, (PViewOf, POwnedPassthrough)) else plan.type
        return self.fill(_view_holder(view), lambda: self.view_rvalue(plan), plan.loc)

    def view_rvalue(self, plan: object) -> MIRRvalue:
        """The value a view holder takes: an alias of a view, a borrow of a
        name's, a global's or an element's storage, a static literal, a
        call's view result, or an alias of the holder an owned operand
        builds. A slice reads its bounds first and borrows its whole receiver."""
        match plan:
            case PElementValue():
                return MIRBorrow(self.element_place(plan.element), plan.element.raises)
            case PViewOf():
                return self.view_rvalue(plan.source)
            case POwnedPlace():
                return MIRBorrow(self.owned_place(plan.place))
            case POwnedLiteral(borrowed=True):
                return MIRConstant(plan.value)
            case POwnedPassthrough():
                return self.view_rvalue(plan.inner)
            case PViewName():
                return MIRAlias(self.bindings[plan.name])
            case PViewField():
                return MIRBorrow(self.place(plan.place), self.chain_raises(plan.place))
            case PViewSlice():
                for bound in plan.bounds:
                    self.value(bound)
                return self.view_rvalue(plan.receiver)
            case PViewCall():
                return self.call(plan.call)
        return MIRAlias(self.owned_operand(plan))

    def owned_place(self, place: PGlobalPlace | PNamePlace) -> MIRPlace:
        """The storage an owned-leaf binding or global denotes: the body's
        own, or what a parameter or global handle borrows."""
        if isinstance(place, PGlobalPlace):
            return MIRPlace(self.globals[place.identity], (MIRDeref(),))
        slot = MIRPlace(self.bindings[place.name])
        return MIRPlace(slot.root, self.storage_steps(slot))

    def through(self, plan: object) -> MIRPlace:
        """The storage an owning sink copies a value from: a name's own
        place, else what the holder of the lent value reaches."""
        if isinstance(plan, POwnedPlace):
            return self.owned_place(plan.place)
        holder = MIRPlace(self.operand(plan))
        return MIRPlace(holder.root, self.storage_steps(holder))

    def owned_operand(self, plan: object) -> MIRSlotId:
        typ, loc = plan.type, plan.loc
        match plan:
            case POwnedPlace():
                return self.holder(_owned_holder(typ), MIRBorrow(self.owned_place(plan.place)), loc)
            case POwnedLiteral(borrowed=True):
                return self.holder(_owned_holder(typ), MIRConstant(plan.value), loc)
            case POwnedCall(borrowed=True):
                # A result that may borrow the call's lent arguments: their referents are its.
                return self.holder(_owned_holder(typ), self.call(plan.call), loc)
            case POwnedPassthrough():
                return self.view_slot(plan)
            case POwnedField():
                return self.holder(_owned_holder(typ), MIRBorrow(self.place(plan.place), self.chain_raises(plan.place)),
                                   loc)
            case PElementValue():
                return self.holder(_owned_holder(typ), MIRBorrow(self.element_place(plan.element),
                                                                 plan.element.raises), loc)
        temporary = self.slot(_owned_storage(typ), storage_duration=self.region)
        self.write(temporary, self.owned_rvalue(plan), loc, MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return self.holder(_owned_holder(typ), MIRBorrow(MIRPlace(temporary)), loc)

    def owned_rvalue(self, plan: object) -> MIRRvalue:
        """The value an owned-leaf expression writes into owned storage."""
        match plan:
            case POwnedPlace():
                return MIRCopy(self.owned_place(plan.place), may_raise=plan.copy_may_raise)
            case POwnedFormConvert():
                return MIRCopy(self.through(plan.source), may_raise=plan.copy_may_raise)
            case POwnedField():
                place = self.place(plan.place)
                return MIRCopy(place, may_raise=bool(plan.copy_may_raise) or reaches_element(place))
            case PElementValue():
                # Copying an element also checks its index.
                return MIRCopy(self.element_place(plan.element), may_raise=True)
            case POwnedLiteral():
                return MIRConstant(plan.value)
            case POwnedPassthrough():
                return MIRCopy(self.through(plan.inner), may_raise=plan.copy_may_raise)
            case PConvert():
                return self.conversion(plan)
            case POwnedCall(borrowed=True):
                # C++ copies the result it was handed by reference into the sink.
                held = self.holder(_owned_holder(plan.type), self.call(plan.call), plan.loc)
                return MIRCopy(MIRPlace(held, (MIRDeref(),)), may_raise=plan.copy_may_raise)
            case POwnedCall():
                return self.call(plan.call)
            case POperation():
                return self.operation(plan)
        raise _unknown(plan)

    # --- calls and arguments ------------------------------------------------

    def call(self, plan: PCall) -> MIRCall:
        summary = plan.summary
        # A method's receiver is parameter 0, lent as the holder of its place.
        arguments = tuple(self.argument(a) for a in plan.arguments)
        return MIRCall(summary, arguments, not summary.normal_return_only, plan.loc, plan.method,
                       self.absent(arguments))

    def absent(self, arguments: tuple[MIRSlotId, ...]) -> frozenset[int]:
        return frozenset(i for i, sid in enumerate(arguments) if sid in self.empty_arguments)

    def argument(self, plan: object) -> MIRSlotId:
        """One operand a call or a construct binds, in the form the check
        pass decided for its parameter."""
        match plan:
            case POptionalOperand():
                return self.optional_operand(plan)
            case PTemporary():
                return self.temp_holders[plan.index]
            case PRecordSource():
                return self.record_holder(plan)
            case PContainer():
                # A container place: lent whole, through a holder of it.
                return self.container_slot(plan)
            case PContainerMove():
                # Owned container storage moved into the temporary the callee takes over.
                temporary = self.slot(plan.storage, storage_duration=self.region)
                self.write(temporary, MIRMove(self.bindings[plan.name]), plan.loc,
                           MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                return temporary
            case PRecordTemporary():
                # A record handed over by value: the temporary the call moves from.
                return self.record_temporary(plan)
            case PLentTemporary():
                # A record built for a lent parameter, through a readonly holder of its temporary.
                storage = self.record_temporary(plan.temporary)
                return self.holder(plan.holder, MIRBorrow(MIRPlace(storage)), plan.temporary.loc)
            case POwnedArgument() if plan.borrowing:
                # Lent for the call: the holder stays live until the call reads it.
                return self.operand(plan.value)
            case POwnedArgument():
                # The callee's own copy: storage of the full expression, handed to the call.
                temporary = self.slot(_owned_storage(plan.type), storage_duration=self.region)
                self.write(temporary, self.owned_rvalue(plan.value), plan.loc,
                           MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                return temporary
        return self.operand(plan)

    def optional_operand(self, plan: POptionalOperand) -> MIRSlotId:
        """An Optional holder of what an Optional parameter's argument
        points at, or of its scalar payload (`_Coverage.optional_operand`)."""
        # A scalar payload lives in the region the call runs in, like a scalar Optional local's.
        dest = self.slot(plan.shape, storage_duration=self.scoped_duration() if plan.scalar else None)
        if plan.scalar:
            self.write(dest, self.optional_value(plan.value), plan.loc,
                       self.payload_write(plan.shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
            return dest
        source = plan.value
        match source:
            case PTemporary():
                value = MIROptionalConstruct(MIRPlace(self.temp_holders[source.index]))
            case PAbsent():
                value = MIROptionalConstruct()
                self.empty_arguments.add(dest)
            case POptionalFieldCopy():
                value = MIROptionalCopy(self.place(source.place))
            case PRecordSource():
                value = MIROptionalConstruct(MIRPlace(self.record_holder(source)))
            case PWrapperCopy():
                value = MIROptionalCopy(MIRPlace(self.bindings[source.name]))
            case _:
                raise _unknown(source)
        self.write(dest, value, plan.loc)
        return dest

    # --- records ------------------------------------------------------------

    def record_value(self, plan: object) -> MIRRvalue:
        match plan:
            case PConstruct():
                args = {name: self.argument(arg) for name, arg in plan.arguments}
                return self.construct(plan.members, args, plan.loc)
            case PRecordCall():
                # The callee's owned result, handed over into the destination.
                return self.call(plan.call)
            case PRecordMove():
                return MIRMove(self.storage[plan.name])
            case PRecordCopy():
                # C++ copies what it reads into the storage.
                place = self.record_source(plan.source)
                return MIRCopy(place, _copy_may_raise(self.layouts, plan.type) or reaches_element(place))
        raise _unknown(plan)

    def record_source(self, plan: PRecordSource) -> MIRPlace:
        """The place of a record source: the storage a name's holder
        reaches, or a member place."""
        place = self.place(plan.place)
        if plan.member:
            return place
        # A name's holder, or a narrowed Optional name's payload, points at it.
        return MIRPlace(place.root, (*place.projections, *self.storage_steps(place)))

    def record_holder(self, plan: PRecordSource) -> MIRSlotId:
        """A holder of a record source for an operation that reads one (a
        lent argument, a copied element): a name's own holder, else a borrow
        of the source's place (a member, a narrowed Optional's payload) at
        the access the check pass decided."""
        if plan.held:
            return self.place(plan.place).root
        return self.fill(plan.holder, lambda: MIRBorrow(self.record_source(plan), self.chain_raises(plan.place)),
                         plan.loc)

    def record_temporary(self, plan: PRecordTemporary) -> MIRSlotId:
        """A record built, handed over by a call, copied or moved into storage
        of the full expression for an operation that moves it out (a
        container element, an owning parameter) or lends it.
        THIR publishes no backing for it: it is MIR's own temporary."""
        storage = self.slot(plan.storage, storage_duration=self.region)
        self.write(storage, self.record_value(plan.value), plan.loc,
                   MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
        return storage

    def construct(self, plan: PMembers, args: Mapping[str, MIRSlotId], loc: object) -> MIRConstruct:
        """The definition decided each member's source: a parameter's
        argument (copied out of a lent holder or moved out of the handed-over
        temporary), an inert constant, or a member built by its own
        constructor over the same arguments into storage of the full
        expression, then moved in. Only the member copies may raise; a
        composed member's own construct carries its copies."""
        fields = []
        for member in plan.members:
            match member:
                case PMemberArgument():
                    fields.append(args[member.name])
                case PMemberComposed():
                    storage = self.slot(member.storage, storage_duration=self.region)
                    self.write(storage, self.construct(member.members, args, loc), loc,
                               MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
                    fields.append(storage)
                case PMemberView():
                    fields.append(self.holder(member.holder, member.source, member.loc))
                case PMemberConstant():
                    fields.append(self.result(member.type, member.source, member.loc))
                case PMemberOptional():
                    fields.append(self.optional_member(member, args, loc))
                case _:
                    raise _unknown(member)
        # An Optional record member takes an Optional holder of what its
        # payload copies; the member copy may raise whatever its source.
        raises = any(_copy_may_raise(self.layouts, payload) for payload in plan.payloads)
        return MIRConstruct(tuple(fields), raises or plan.may_raise)

    def optional_member(self, plan: PMemberOptional, args: Mapping[str, MIRSlotId], loc: object) -> MIRSlotId:
        """The operand of an Optional record member a constructor leaves
        empty or engages with a payload its own constructor builds: an
        Optional holder, empty or borrowing that payload built into storage
        of the full expression."""
        dest = self.slot(plan.shape)
        if plan.composed is not None:
            storage_shape, holder_shape, members = plan.composed
            storage = self.slot(storage_shape, storage_duration=self.region)
            self.write(storage, self.construct(members, args, loc), loc,
                       MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
            holder = self.holder(holder_shape, MIRBorrow(MIRPlace(storage)), loc)
            self.write(dest, MIROptionalConstruct(MIRPlace(holder)), loc)
        else:
            self.write(dest, MIROptionalConstruct(), loc)
        return dest

    def record_backing(self, backing: object, value: object, mode: MIRRecordWriteMode) -> MIRRvalue:
        """An Optional's payload built into its own record storage."""
        storage = self.slot(backing.storage, storage_duration=(
            self.region if mode is MIRRecordWriteMode.INITIALIZE_REGION else MIRStorageDuration.BODY))
        self.write(storage, self.record_value(value), backing.loc, MIRRecordWrite(mode))
        reference = self.holder(backing.holder, MIRBorrow(MIRPlace(storage)), backing.loc)
        return MIROptionalConstruct(MIRPlace(reference))

    # --- wrappers, tuples, containers, views of regions ---------------------

    def optional_value(self, plan: object) -> MIRRvalue:
        """What an Optional binding takes (`_Coverage.optional_source`)."""
        match plan:
            case PAbsent():
                return MIROptionalConstruct()
            case PWrapperLiteral():
                return MIROptionalConstruct(MIRPlace(self.result(plan.payload, MIRConstant(plan.value), plan.loc)))
            case PWrapperCopy():
                return MIROptionalCopy(MIRPlace(self.bindings[plan.name]))
            case POptionalFieldCopy():
                # A holder of the field's inline payload: a borrow, no copy.
                return MIROptionalCopy(self.place(plan.place))
            case PWrapperCapture():
                return MIROptionalConstruct(MIRPlace(self.bindings[plan.name]))
            case PWrapperValue():
                return MIROptionalConstruct(MIRPlace(self.value(plan.value)))
        raise _unknown(plan)

    def union_value(self, plan: object) -> MIRRvalue:
        """What a union binding takes, engaging the alternative the check pass chose."""
        match plan:
            case PWrapperLiteral():
                source = None if plan.payload is None else MIRPlace(self.result(plan.payload, MIRConstant(plan.value),
                                                                                plan.loc))
                return MIRUnionConstruct(plan.alternative, source)
            case PWrapperCopy():
                return MIRUnionCopy(MIRPlace(self.bindings[plan.name]))
            case PAbsent():
                return MIRUnionConstruct(plan.alternative)
            case PWrapperCapture():
                return MIRUnionConstruct(plan.alternative, MIRPlace(self.bindings[plan.name]))
            case PWrapperValue():
                return MIRUnionConstruct(plan.alternative, MIRPlace(self.value(plan.value)))
        raise _unknown(plan)

    def optional_field_value(self, plan: POptionalFieldValue) -> MIRRvalue:
        """What an Optional record field takes: empty, a copy of another
        Optional's payload, or a record built into storage of the statement
        and moved into the payload."""
        match plan.value:
            case PAbsent():
                return MIROptionalConstruct()
            case PRecordTemporary():
                return MIROptionalConstruct(MIRPlace(self.record_temporary(plan.value)))
        source = self.place(plan.value)
        return MIROptionalCopy(source, _copy_may_raise(self.layouts, plan.payload) or reaches_element(source))

    def tuple_value(self, plan: PTupleName | PTupleLiteral) -> MIRSlotId:
        dest = self.slot(plan.shape)
        if isinstance(plan, PTupleName):
            value = MIRTupleCopy(self.bindings[plan.name])
        else:
            value = MIRTupleConstruct(tuple(self.place(e).root if isinstance(e, PNamePlace) else self.value(e)
                                            for e in plan.elements))
        self.write(dest, value, plan.loc)
        return dest

    def container_construct(self, plan: PContainerLiteral) -> MIRConstruct:
        """A container literal: one operand per element (a dict's keys and
        values alternating), each in its member's form -- an inert leaf by
        value, an owned leaf through a holder its slot copies, a record
        built into a temporary it moves from or copied through a holder."""
        fields = []
        for operand in plan.operands:
            match operand:
                case PRecordSource():
                    fields.append(self.record_holder(operand))
                case PRecordTemporary():
                    fields.append(self.record_temporary(operand))
                case _:
                    fields.append(self.operand(operand))
        return MIRConstruct(tuple(fields), may_raise=True)

    def span_rvalue(self, plan: object) -> MIRRvalue:
        """The value a Span holder takes: an alias of a Span of its own
        type, else a borrow of the region its source views (an unstepped
        slice reads its bounds first)."""
        match plan:
            case PSpanAlias():
                return MIRAlias(self.bindings[plan.name])
            case PSpanRegion():
                return MIRBorrow(MIRPlace(self.bindings[plan.name], (MIRContainerElements(),)))
            case PSpanSlice():
                for bound in plan.bounds:
                    self.value(bound)
                root = self.container_root(plan.receiver)
                return MIRBorrow(MIRPlace(root.root, (*root.projections, MIRContainerElements())))
        raise _unknown(plan)

    def borrowed_expression(self, plan: object) -> MIRSlotId:
        match plan:
            case PAnchored():
                self.initialize(plan.temporaries)
                return self.borrowed_expression(plan.body)
            case PTemporary():
                return self.temp_holders[plan.index]
            case PBorrowedCall():
                return self.fill(plan.holder, lambda: self.call(plan.call), plan.loc)
            case PBorrowedField():
                # A member place: the result holder borrows it.
                return self.fill(plan.holder, lambda: MIRBorrow(self.place(plan.place), self.chain_raises(plan.place)),
                                 plan.loc)
            case PBorrowedSelect():
                condition = self.value(plan.cond)
                dest = self.slot(plan.holder)
                yes, no, join = self.block(), self.block(), self.block()
                self.branch(condition, yes.id, no.id, plan.loc)
                for block, arm in ((yes, plan.then), (no, plan.orelse)):
                    self.current = block
                    source = self.borrowed_expression(arm)
                    self.write(dest, MIRAlias(source), arm.loc)
                    self.end(MIRGoto(join.id, arm.loc))
                self.current = join
                return dest
            case PBorrowedName():
                return self.bindings[plan.name]
            case PContainer():
                # A container place returned by reference.
                return self.container_slot(plan)
        raise _unknown(plan)

    def rvalue(self, plan: object) -> MIRRvalue:
        """What a write stores from a value, in the value's own form."""
        match plan:
            case PContainerLiteral():
                return self.container_construct(plan)
            case PElementPlace():
                return MIRBorrow(self.element_place(plan), plan.raises)
            case POptionalFieldValue():
                return self.optional_field_value(plan)
            case PTupleName() | PTupleLiteral():
                return MIRTupleCopy(self.tuple_value(plan))
        if isinstance(plan, _SPAN_PLANS):
            return self.span_rvalue(plan)
        if isinstance(plan, _RECORD_VALUES):
            return self.record_value(plan)
        if _owned(plan):
            return self.owned_rvalue(plan)
        if _view(plan):
            return self.view_rvalue(plan)
        return MIRRead(MIRPlace(self.value(plan)))

    # --- full expressions ---------------------------------------------------

    @contextmanager
    def full_expression(self, plan: PFullExpression) -> Iterator[None]:
        if not plan.needs_region:
            yield
            return
        entry, after = self.block(), self.block()
        self.end(MIRGoto(entry.id, plan.loc))
        self.current = entry
        with self.scope():
            yield
            self.end(MIRGoto(after.id, plan.loc))
        self.current = after

    def full_expression_value(self, plan: PFullExpression) -> MIRSlotId:
        if not plan.needs_region:
            return self.value(plan.body)
        result = self.slot(_scalar(plan.body.type))
        with self.full_expression(plan):
            self.write(result, MIRRead(MIRPlace(self.value(plan.body))), plan.loc)
        return result

    # --- statements ---------------------------------------------------------

    def stmts(self, stmts: tuple[object, ...]) -> None:
        for stmt in stmts:
            if self.current is None:
                # The check pass inspected every retained unreachable statement.
                break
            self.stmt(stmt)

    def stmt(self, stmt: object) -> None:
        loc = getattr(stmt, "loc", None)
        match stmt:
            case PStatementTemporaries() if self.plan.planned:
                self.declare_temporaries(stmt)
            case PStatementTemporaries():
                pass
            case PDeclare():
                dest = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.binding.name,
                                 storage_duration=self.placed(stmt.placement))
                if stmt.value is not None:
                    fact = self.owned_initialization(dest) if stmt.placement is not None else None
                    with self.full_expression(stmt.value):
                        self.write(dest, self.rvalue(stmt.value.body), loc, fact)
                self.bindings[stmt.binding.name] = dest
            case PContainerAlias():
                dest = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.binding.name)
                root = stmt.source.root
                if isinstance(root, PContainerCall):
                    # The alias binds the container the call returns by reference.
                    source = self.container_slot(stmt.source)
                    self.borrow_operation(stmt.operation)
                    self.write(dest, MIRAlias(source), loc)
                else:
                    self.write(dest, MIRAlias(self.bindings[root.name]) if isinstance(root, PNamePlace)
                               else MIRBorrow(self.container_root(stmt.source)), loc)
                self.bindings[stmt.binding.name] = dest
            case PNarrowAlias():
                plan = stmt.binding
                source = self.place(stmt.source)
                dest = self.slot(plan.shape, MIRSlotKind.LOCAL, plan.name, alias_source=source if stmt.aliased else None)
                self.write(dest, MIRUnionExtract(source), loc)
                self.bindings[plan.name] = dest
            case PUnionDeclare():
                dest = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.binding.name,
                                 storage_duration=self.declared_storage(stmt))
                self.write(dest, self.union_value(stmt.value), loc,
                           self.payload_write(stmt.binding.shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
                self.bindings[stmt.binding.name] = dest
            case PUnionAssign():
                dest = self.bindings[stmt.name]
                self.write(dest, self.union_value(stmt.value), loc,
                           self.payload_write(stmt.inline_payload, MIRPayloadWriteMode.ASSIGN))
            case POptionalDeclare():
                dest = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.binding.name,
                                 storage_duration=self.declared_storage(stmt))
                value = (self.record_backing(stmt.backing, stmt.value, self.initial_mode())
                         if stmt.backing is not None else self.optional_value(stmt.value))
                if stmt.operation is not None:
                    # The holder points into the field: a loan replacing the field ends.
                    self.borrow_operation(stmt.operation)
                self.write(dest, value, loc,
                           self.payload_write(stmt.binding.shape.scalar_wrapper, MIRPayloadWriteMode.INITIALIZE))
                self.bindings[stmt.binding.name] = dest
            case POptionalAssign():
                dest = self.bindings[stmt.name]
                if stmt.backing is not None:
                    self.write(dest, self.record_backing(stmt.backing, stmt.value, MIRRecordWriteMode.OWN_SITE), loc)
                elif stmt.in_place:
                    self.write(MIRPlace(dest, PAYLOAD_RECORD), self.record_value(stmt.value), loc,
                               MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, dest))
                else:
                    self.write(dest, self.optional_value(stmt.value), loc,
                               self.payload_write(stmt.inline_payload, MIRPayloadWriteMode.ASSIGN))
            case POwnedRecordDeclare():
                plan = stmt.binding
                storage = self.slot(plan.backing, storage_duration=self.declared_storage(stmt))
                mode = MIRRecordWriteMode.OWN_SITE if stmt.own_site else self.initial_mode()
                with self.full_expression(stmt.value):
                    self.write(storage, self.record_value(stmt.value.body), loc, MIRRecordWrite(mode))
                holder = self.slot(plan.shape, MIRSlotKind.LOCAL, plan.name)
                self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                self.storage[plan.name] = storage
                self.bindings[plan.name] = holder
            case POptionalRecordAssign():
                storage = self.storage[stmt.name]
                self.write(storage, self.record_value(stmt.value), loc,
                           MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
                self.write(self.bindings[stmt.name], MIRBorrow(MIRPlace(storage)), loc)
            case PReplacement():
                holder = self.bindings[stmt.name]
                with self.full_expression(stmt.value):
                    value = self.record_value(stmt.value.body)
                    if stmt.storage is not None:
                        storage = self.slot(stmt.storage, storage_duration=MIRStorageDuration.BODY)
                        self.write(storage, value, loc, MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))
                        self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                    else:
                        self.write(MIRPlace(holder, (MIRDeref(),)), value, loc,
                                   MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder))
            case PBorrowBinding():
                value = MIRBorrow(self.place(stmt.source))
                if stmt.deref:
                    # A tuple member a parameter captures points at the record.
                    value = MIRBorrow(MIRPlace(value.source.root, (*value.source.projections, MIRDeref())))
                if stmt.binding is not None:
                    self.bindings[stmt.name] = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.name)
                self.borrow_operation(stmt.operation)
                self.write(self.bindings[stmt.name], value, loc)
            case PTupleAlias():
                self.bindings[stmt.name] = self.bindings[stmt.source]
            case PBorrowedBinding():
                source = self.borrowed_expression(stmt.value)
                if stmt.binding is not None:
                    self.bindings[stmt.name] = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.name)
                self.borrow_operation(stmt.operation)
                self.write(self.bindings[stmt.name], MIRAlias(source), loc)
            case POwnedTupleDeclare():
                dest = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.binding.name,
                                 storage_duration=self.scoped_duration())
                value = MIRTupleConstruct(tuple(self.record_value(e) if isinstance(e, _RECORD_VALUES)
                                                else self.value(e) for e in stmt.value.elements))
                self.write(dest, value, loc, MIRTupleInitialization())
                self.bindings[stmt.binding.name] = dest
            case PAssign():
                target = self.place(stmt.target)
                fact = MIRRecordWrite(MIRRecordWriteMode.IN_PLACE) if stmt.in_place else None
                with self.full_expression(stmt.value):
                    self.write(target, self.rvalue(stmt.value.body), loc, fact)
            case PScalarAssign():
                with self.full_expression(stmt.value):
                    self.write(self.place(stmt.target), MIRRead(MIRPlace(self.value(stmt.value.body))), loc)
            case PViewMemberAssign():
                target = self.place(stmt.target)
                with self.full_expression(stmt.value):
                    self.write(target, MIRAlias(self.view_slot(stmt.value.body)), loc)
            case PExpressionStatement():
                with self.full_expression(stmt.value):
                    self.effect(stmt.value.body, loc)
            case PReturnHolder():
                # The result holder outlives the full expression, whose
                # temporaries a returned view must not reach.
                holder = self.slot(stmt.holder)
                with self.full_expression(stmt.value):
                    self.write(holder, self.rvalue(stmt.value.body), loc)
                self.end(MIRReturn(holder, loc))
            case POwnedReturn():
                self.owned_return(stmt)
            case PReturnBorrowed():
                value = self.borrowed_expression(stmt.value)
                if stmt.operation is not None:
                    self.borrow_operation(stmt.operation)
                self.end(MIRReturn(value, loc))
            case PReturnValue():
                self.end(MIRReturn(None if stmt.value is None else self.full_expression_value(stmt.value), loc))
            case PIf() if self.plan.planned:
                self.planned_if(stmt)
            case PIf():
                self.if_(stmt)
            case PWhile() if self.plan.planned and stmt.iteration is not None:
                self.planned_while(stmt)
            case PWhile():
                self.while_(stmt)
            case PRangeLoop():
                self.range_loop(stmt)
            case PForEach():
                self.native_loop(stmt)
            case PParamCopy():
                plan = stmt.binding
                param = self.bindings[plan.name]
                local = self.slot(plan.shape, MIRSlotKind.LOCAL, plan.name, storage_duration=self.scoped_duration())
                self.write(local, MIRCopy(MIRPlace(param, (MIRDeref(),)), may_raise=stmt.copy_may_raise),
                           loc, self.owned_initialization(local))
                self.bindings[plan.name] = local
            case PAppend():
                target = self.place(stmt.target)
                with self.full_expression(stmt.value):
                    value = self.operand(stmt.value.body)
                    current = self.holder(_owned_holder(stmt.type), MIRBorrow(target), loc)
                    self.write(target, MIROp("+=", (current, value), may_raise=True), loc,
                               MIRRecordWrite(MIRRecordWriteMode.IN_PLACE))
            case PJump():
                self.end(MIRGoto(self.loops[-1][0 if stmt.head else 1], loc))
            case _:
                raise _unknown(stmt)

    def effect(self, body: object, loc: object) -> None:
        """A full expression evaluated for its effect."""
        match body:
            case PTemporaryRecordPlace():
                self.place(body)
            case PCallStatement():
                self.current.statements.append(MIRCallStmt(self.call(body.call), loc))
            case PPrint():
                arguments = tuple(self.operand(arg) for arg in body.arguments)
                self.current.statements.append(MIRPrint(arguments, loc))
            case PSetItemCall():
                # A `__setitem__` that may move elements: the stub call, writing the receiver's structure.
                self.current.statements.append(MIRCallStmt(self.call(body.call), loc))
            case PElementStore():
                # `xs[i] = v`: the value in the member's form (Python evaluates
                # it first), then the index, replaces the element region in
                # place under the container's root.
                value = self.rvalue(body.value)
                place = self.element_place(body.place)
                self.write(place, value, loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, place.root))
            case _:
                self.value(body)

    def owned_return(self, stmt: POwnedReturn) -> None:
        """Return an owned result: a binding's own storage (a record's
        backing, a container or owned-leaf binding itself) leaves the body
        itself -- the move is C++'s (or elided), not an event of the body;
        anything else is copied or built into a result slot."""
        body = stmt.value.body
        if isinstance(body, PMoveOut):
            self.end(MIRReturn((self.storage if stmt.backing else self.bindings)[body.name], stmt.loc))
            return
        dest = self.slot(stmt.result, storage_duration=self.scoped_duration())
        with self.full_expression(stmt.value):
            self.write(dest, self.rvalue(body), stmt.write_loc, self.owned_initialization(dest))
        self.end(MIRReturn(dest, stmt.loc))

    # --- control flow -------------------------------------------------------

    def if_(self, stmt: PIf) -> None:
        loc = stmt.loc
        self.hoists(stmt.hoists, loc)
        cond = self.full_expression_value(stmt.condition)
        yes, no = self.block(), self.block()
        self.branch(cond, yes.id, no.id, loc)
        exits = []
        for block, arm in ((yes, stmt.then), (no, stmt.orelse)):
            self.current = block
            self.scoped(arm)
            if self.current is not None:
                exits.append(self.current)
        if exits:
            join = self.block()
            for block in exits:
                self.current = block
                self.end(MIRGoto(join.id, loc))
            self.current = join
        else:
            self.current = None

    def planned_if(self, stmt: PIf) -> None:
        self.hoists(stmt.hoists, stmt.loc)
        chain = [stmt]
        while chain[-1].elif_:
            chain.append(chain[-1].orelse[0])
        join = self.block()

        def arm(index: int) -> None:
            node = chain[index]
            if index:
                # An elif is an If of its own, whose hoists the check pass planned too.
                self.hoists(node.hoists, node.loc)
            self.declare_temporaries(node.temporaries)
            condition = self.full_expression_value(node.condition)
            yes, no = self.block(), self.block()
            self.branch(condition, yes.id, no.id, node.loc)
            self.current = yes
            self.scoped(node.then, node.then_scope)
            if self.current is not None:
                self.end(MIRGoto(join.id, node.loc))
            self.current = no
            if index + 1 < len(chain):
                following = chain[index + 1]
                if following.condition_scope is not None:
                    with self.scope(following.condition_scope):
                        arm(index + 1)
                else:
                    arm(index + 1)
            else:
                if node.else_scope is not None:
                    self.scoped(node.orelse, node.else_scope)
                if self.current is not None:
                    self.end(MIRGoto(join.id, node.loc))

        arm(0)
        self.current = join

    def while_(self, stmt: PWhile) -> None:
        loc = stmt.loc
        self.hoists(stmt.hoists, loc)
        cond_block, body, normal, after = self.block(), self.block(), self.block(), self.block()
        self.end(MIRGoto(cond_block.id, loc))
        self.current = cond_block
        self.branch(self.full_expression_value(stmt.condition), body.id, normal.id, loc)
        self.current = body
        self.loops.append((cond_block.id, after.id))
        self.scoped(stmt.body, stmt.loop_scope)
        self.loops.pop()
        if self.current is not None:
            self.end(MIRGoto(cond_block.id, loc))
        self.current = normal
        self.scoped(stmt.orelse, stmt.else_scope)
        if self.current is not None:
            self.end(MIRGoto(after.id, loc))
        self.current = after

    def planned_while(self, stmt: PWhile) -> None:
        self.hoists(stmt.hoists, stmt.loc)
        bridge, condition, normal, after = self.block(), self.block(), self.block(), self.block()
        self.end(MIRGoto(bridge.id, stmt.loc))
        self.current = bridge
        self.end(MIRGoto(condition.id, stmt.loc))
        self.current = condition
        with self.scope(stmt.iteration):
            self.declare_temporaries(stmt.temporaries)
            value = self.full_expression_value(stmt.condition)
            body = self.block()
            self.branch(value, body.id, normal.id, stmt.loc)
            self.current = body
            self.loops.append((bridge.id, after.id))
            self.stmts(stmt.body)
            self.loops.pop()
            if self.current is not None:
                self.end(MIRGoto(bridge.id, stmt.loc))
        self.current = normal
        if stmt.else_scope is not None:
            self.scoped(stmt.orelse, stmt.else_scope)
        if self.current is not None:
            self.end(MIRGoto(after.id, stmt.loc))
        self.current = after

    def range_loop(self, stmt: PRangeLoop) -> None:
        loc = stmt.loc
        self.hoists(stmt.hoists, loc)
        start = self.value(stmt.start) if stmt.start is not None else self.result(INT32, MIRConstant(0), loc)
        stop = self.value(stmt.stop)
        entry, normal, after = self.block(), self.block(), self.block()
        self.end(MIRGoto(entry.id, loc))
        self.current = entry
        with self.scope(stmt.counter_scope):
            name = stmt.name
            counter = (self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, name) if stmt.direct
                       else self.slot(_scalar(INT32), MIRSlotKind.LOCAL))
            self.write(counter, MIRRead(MIRPlace(start)), loc)
            if stmt.direct:
                self.bindings[name] = counter
            head, body, advance = self.block(), self.block(), self.block()
            self.end(MIRGoto(head.id, loc))
            self.current = head
            cond = self.result(BOOL, MIRCompare("<" if stmt.step == 1 else ">", counter, stop), loc)
            self.branch(cond, body.id, normal.id, loc)
            self.current = body
            self.loops.append((advance.id, after.id))
            with self.scope(stmt.loop_scope):
                if not stmt.direct:
                    if stmt.binding is not None:
                        self.bindings[name] = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, name)
                    self.write(self.bindings[name], MIRRead(MIRPlace(counter)), loc)
                self.stmts(stmt.body)
            self.loops.pop()
            if self.current is not None:
                self.end(MIRGoto(advance.id, loc))
            self.current = advance
            self.write(counter, MIRRangeAdvance(counter, stmt.step), loc)
            self.end(MIRGoto(head.id, loc))
        self.current = normal
        self.scoped(stmt.orelse, stmt.else_scope)
        if self.current is not None:
            self.end(MIRGoto(after.id, loc))
        self.current = after

    def native_loop(self, stmt: PForEach) -> None:
        loc = stmt.loc
        self.hoists(stmt.hoists, loc)
        source = self.container_slot(stmt.source)
        iterator = self.slot(stmt.iterator)
        self.write(iterator, MIRIteratorInit(source), loc)
        head, body, advance, normal, after = (self.block() for _ in range(5))
        self.end(MIRGoto(head.id, loc))
        self.current = head
        self.branch(self.result(BOOL, MIRIteratorHasNext(iterator), loc), body.id, normal.id, loc)
        self.current = body
        self.loops.append((advance.id, after.id))
        with self.scope(stmt.loop_scope):
            if stmt.binding is not None:
                self.bindings[stmt.name] = self.slot(stmt.binding.shape, MIRSlotKind.LOCAL, stmt.name)
            self.write(self.bindings[stmt.name], MIRIteratorRead(iterator), loc)
            self.stmts(stmt.body)
        self.loops.pop()
        if self.current is not None:
            self.end(MIRGoto(advance.id, loc))
        self.current = advance
        self.write(iterator, MIRIteratorAdvance(iterator), loc)
        self.end(MIRGoto(head.id, loc))
        self.current = normal
        self.scoped(stmt.orelse, stmt.else_scope)
        if self.current is not None:
            self.end(MIRGoto(after.id, loc))
        self.current = after

    def hoists(self, hoists: tuple[PHoist, ...], loc: object) -> None:
        for hoist in hoists:
            plan = hoist.binding
            if plan.backing is not None:
                # An Optional record's backing, empty until a branch assigns it.
                storage = self.slot(plan.backing, MIRSlotKind.LOCAL, storage_duration=self.scoped_duration(),
                                    record_storage=MIRRecordStorageKind.OPTIONAL)
                self.current.statements.append(MIRRecordStorageInit(MIRPlace(storage), loc))
                self.storage[plan.name] = storage
                self.bindings[plan.name] = self.slot(plan.shape, MIRSlotKind.LOCAL, plan.name)
                continue
            dest = self.slot(plan.shape, MIRSlotKind.LOCAL, plan.name,
                             storage_duration=self.scoped_duration() if hoist.default is not None else None)
            self.bindings[plan.name] = dest
            if hoist.default is not None:
                alternative, value = hoist.default
                self.current.statements.append(MIRStorageInit(MIRPlace(dest), alternative, MIRConstant(value), loc))

    @contextmanager
    def scope(self, temporaries: int | None = None) -> Iterator[None]:
        """A scope of the body: a region, and the temporary plan scope it opens."""
        bindings, storage, region = self.bindings.copy(), self.storage.copy(), self.region
        temp_scope = self.temp_scope
        assert self.current is not None and not self.current.statements
        self.region = MIRRegionId(self.body, len(self.regions))
        self.regions.append(MIRRegion(self.region, region, self.current.id))
        self.current.region = self.region
        if self.plan.planned and temporaries is not None:
            self.temp_regions[temporaries] = self.region
            self.temp_scope = temporaries
        try:
            yield
        finally:
            self.bindings, self.storage, self.region = bindings, storage, region
            self.temp_scope = temp_scope

    def scoped(self, stmts: tuple[object, ...], temporaries: int | None = None) -> None:
        with self.scope(temporaries):
            self.stmts(stmts)

    # --- the body -----------------------------------------------------------

    def build(self) -> MIRFunction:
        plan = self.plan
        if plan.receiver is not None:
            self.bindings["self"] = self.slot(plan.receiver.binding.shape, MIRSlotKind.PARAMETER, "self",
                                              passing=plan.receiver.passing)
        for parameter in plan.parameters:
            binding = parameter.binding
            if binding.backing is not None:
                # The caller materialized the record before entry; the body
                # owns it and reaches it through a holder borrowed at entry.
                storage = self.slot(binding.backing, MIRSlotKind.PARAMETER, binding.name,
                                    storage_duration=parameter.storage_duration, passing=parameter.passing)
                holder = self.slot(binding.shape, MIRSlotKind.LOCAL)
                self.write(holder, MIRBorrow(MIRPlace(storage)), None)
                self.storage[binding.name] = storage
                self.bindings[binding.name] = holder
                continue
            self.bindings[binding.name] = self.slot(binding.shape, MIRSlotKind.PARAMETER, binding.name,
                                                    passing=parameter.passing,
                                                    storage_duration=parameter.storage_duration)
        for identity, binding in plan.globals:
            self.globals[identity] = self.slot(binding.shape, MIRSlotKind.GLOBAL, binding.name, global_id=identity)
        receiver_init = None
        if plan.initializers is not None:
            receiver_init = MIRReceiverInit(self.bindings["self"], tuple(
                self.member_init(init) for init in plan.initializers))
        self.stmts(plan.statements)
        reachable: set[MIRBlockId] = set()
        pending = [self.blocks[0].id]
        while pending:
            bid = pending.pop()
            if bid in reachable:
                continue
            reachable.add(bid)
            term = self.blocks[bid.index].terminator
            if term is not None:
                pending.extend(successors(term))
        if self.current is not None and self.current.id in reachable:
            if not plan.void:
                raise MIRFallthroughError("non-void fallthrough")
            self.end(MIRReturn())
        blocks = []
        for b in self.blocks:
            if b.id not in reachable:
                continue
            assert b.terminator is not None
            blocks.append(MIRBlock(b.id, tuple(b.statements), b.terminator, b.region))
        slots = {s.id: s for s in self.slots}
        records = tuple(self.layouts.values())
        fn = MIRFunction(self.body, plan.return_type, self.reachable_slots(blocks, receiver_init), tuple(blocks),
                         self.blocks[0].id, records, receiver_init, plan.kind,
                         tuple(r for r in self.regions if r.entry in reachable), plan.callees, plan.result,
                         body_may_raise(blocks, slots, {r.type: r for r in records}, receiver_init))
        validate_function(fn)
        return fn

    def member_init(self, init: MIRFieldInitializer) -> MIRMemberInit:
        """One member's entry initialization from its verified initializer:
        a parameter's slot (an `Own[R]` parameter's storage, not the holder
        the body reaches it through), a constant, a container literal built
        over parameters, or a record member built by its own constructor,
        initialized field by field like the receiver."""
        match init.source:
            case str():
                source = self.storage.get(init.source, self.bindings[init.source])
            case tuple():
                source = MIRConstruct(tuple(self.bindings[name] for name in init.source), True)
            case MIRComposedConstruct():
                source = MIRMemberInits(tuple(self.member_init(member) for member in init.source.initializers))
            case _:
                source = init.source
        return MIRMemberInit(source, init.mode, init.may_raise, init.loc)

    def reachable_slots(self, blocks: list[MIRBlock], receiver: MIRReceiverInit | None) -> tuple[MIRSlot, ...]:
        retained = {s.id for s in self.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
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
            source = self.slots[pending.pop().index].alias_source
            if source is not None and source.root not in retained:
                retained.add(source.root)
                pending.append(source.root)
        # Preserve IDs: consumers key by identity, and dead branches can leave gaps.
        return tuple(s for s in self.slots if s.id in retained)
