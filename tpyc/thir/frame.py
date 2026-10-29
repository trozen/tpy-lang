"""The resumable frame as THIR: a generator / coroutine body's state
machine, decided once on the THIR side and printed by codegen.

A resumable body is a COARSE CFG: blocks split only at suspensions and
their joins, a compound statement with no suspension staying one leaf.
`THIRResumableFrame` publishes it together with the frame's placement
decisions (parameter capture, frame locals, the loop / with / pointer-slot
/ try-finally holders) at the moment they are made today, before any leaf
of the body is lowered; the leaves follow as `THIRResumableLeaves`
(nodes.py) once the body is lowered.

C++'s rule that case labels inside try blocks are not reachable from
outside the try is why each block carries its region stack: the printer
replays the try/except/finally/with stack active at a suspension inside
every resume case (the same pattern Roslyn's async rewriter uses).

A suspension is one terminator (`THIRSuspend`) whose payload says whether
it is an await (`THIRAwaitSuspension`) or a yield (`THIRYieldSuspension`):
the kind is a property of the operation, not a second operation.

Every position where user source renders -- a block's leaf statement, a
branch condition, a return, an await's operands, a loop or `with` header
expression, a finally helper's statement, a nested def -- is a
`THIRLeafSlot` the frame owns. The leaves (`THIRResumableLeaves`) are keyed
by these slots, so a reader reaches a position's lowering from the frame
alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple, Union

from ..identity_map import IdentityMap, IdentitySet
from ..resumable_shape import ResumableShape
from ..parse.nodes import (
    TpyExceptHandler, TpyExpr, TpyForEach, TpyMatch, TpyMethodCall, TpyRaise,
    TpyReturn, TpyStmt, TpyWithItem, TpyYield, TpyAwait,
)

if TYPE_CHECKING:
    from ..parse.nodes import SourceLocation, TpyFunction
    from ..typesys import TpyType
    from .nodes import THIRBorrowedRecord, THIRParam


# -- Frame-local placement plan.

class FrameLocalKind(Enum):
    """The C++ field form backing one hoisted frame local.

    One verdict per local, decided once (the frame builder's `_frame_layout`) and
    consumed by every reader of the frame-field form: the struct
    field-decl emit, `setup_resumable_frame_locals`, and THIR admission.
    A consumer re-deriving the form from the local's TYPE is the defect
    class this plan exists to remove -- the form depends on binding facts
    (write sources, loop machinery, prescan sets) the type alone cannot
    recover.

      * VALUE            -- bare `T name;` (value types).
      * OWNED_STR        -- `std::string name;`: a `with ... as` str target
                            whose `__enter__` result would dangle as a view.
      * PTR_ALIAS        -- `T* name = nullptr;` aliasing live storage
                            (pointer-form loop var, unpack target, or
                            statement-level borrow alias).
      * OPT_PTR          -- `T* name = nullptr;` for a pointer-repr
                            Optional local (nullptr doubles as None).
      * REBIND_PTR       -- `T* name = nullptr;` for a plain non-value
                            local some rvalue rebind of which needs storage
                            of its own (sema's alias-rebind OWN verdict):
                            writes materialize in per-site `__ptr_slot_fN`
                            fields like OPT_PTR's, or assign through the
                            pointer where the verdict is IN_PLACE.
      * BORROW_TUPLE     -- `std::tuple<..., T*> name;` borrow-form tuple.
      * OWNING_TUPLE_SLOT-- `::tpy::frame_slot<std::tuple<...storage...>>`:
                            an owning tuple (Own element / owned call
                            result); emplace writes, `(*name)` reads.
      * MIXED_TUPLE_SLOT -- the owning slot of a MIXED tuple
                            (`tuple[Own[A], B]`), whose payload is the mixed
                            render `std::tuple<A, B*>` rather than a
                            fully-owned storage tuple: the owned element is
                            held by value, the borrowed one stays a pointer
                            into the caller's object. Reads split per element
                            (`.` owned, `->` borrowed), which is what
                            separates it from OWNING_TUPLE_SLOT.
      * FRAME_SLOT       -- `::tpy::frame_slot<T> name;` owning slot for
                            plain non-value locals; emplace writes,
                            `(*name)` reads.
      * SOURCE_FORM_SLOT -- a loop var whose alias-vs-own choice belongs to
                            C++: `::tpy::frame_slot<payload>` where the
                            payload spelling comes from the iteration
                            source's protocol (`for_elem_deref_t` /
                            `for_elem_next_t`) and frame_slot's
                            specializations supply either form at
                            instantiation.
      * PROTOCOL         -- a static-protocol-typed local: no concrete C++
                            backing exists for a frame FIELD (rendering one
                            is a user-facing CodeGenError at the struct
                            emit); body-context seeding treats it like
                            FRAME_SLOT, matching the peephole path where no
                            field is ever rendered.
    """
    VALUE = "value"
    OWNED_STR = "owned_str"
    PTR_ALIAS = "ptr_alias"
    OPT_PTR = "opt_ptr"
    REBIND_PTR = "rebind_ptr"
    BORROW_TUPLE = "borrow_tuple"
    OWNING_TUPLE_SLOT = "owning_tuple_slot"
    MIXED_TUPLE_SLOT = "mixed_tuple_slot"
    FRAME_SLOT = "frame_slot"
    SOURCE_FORM_SLOT = "source_form_slot"
    PROTOCOL = "protocol"


# The kinds a `::tpy::frame_slot<T>` field backs: writes emplace, reads go
# through `(*name)`. PROTOCOL has no field (its struct emit is a user
# error), but its body seeding is FRAME_SLOT's.
SLOT_BACKED_KINDS = frozenset({
    FrameLocalKind.SOURCE_FORM_SLOT, FrameLocalKind.OWNING_TUPLE_SLOT,
    FrameLocalKind.MIXED_TUPLE_SLOT, FrameLocalKind.FRAME_SLOT,
    FrameLocalKind.PROTOCOL})

# The kinds whose field is a `T*`: a read of the local already renders as
# the pointer, so a borrow of it binds the render as it is.
POINTER_LOCAL_KINDS = frozenset({
    FrameLocalKind.PTR_ALIAS, FrameLocalKind.OPT_PTR, FrameLocalKind.REBIND_PTR})

# The pointer-form kinds a `with ... as` target aims at its enter result
# through. OPT_PTR is not one: a nullable target binds with `=`.
WITH_POINTER_KINDS = frozenset({FrameLocalKind.PTR_ALIAS,
                                FrameLocalKind.REBIND_PTR})


@dataclass(frozen=True)
class FrameLocalLayout:
    """Placement verdict for one hoisted local. `const` applies to the
    pointer kinds (PTR_ALIAS / OPT_PTR: `const T*` when the local is one of
    the frame's const bindings). `payload` is the field's C++ payload spelling
    for the kinds that cannot re-derive it from the local's type --
    SOURCE_FORM_SLOT (spelled from the iteration source), MIXED_TUPLE_SLOT (the
    mixed render), and a PTR_ALIAS whose pointee only the iteration source
    knows (an unpack holder pointing into the step result); None otherwise.

    `effective_type` is the local's tuple type with per-element ownership made
    explicit (`Own[...]` on the elements the frame owns) for a tuple whose
    declared type cannot say so -- a literal-bound owning local. None whenever
    the declared type is already the whole story."""
    kind: FrameLocalKind
    const: bool = False
    payload: 'str | None' = None
    effective_type: 'TpyType | None' = None


class LeafRole(Enum):
    """What a leaf slot's position renders, which fixes the form of its
    lowering and whether the printer flushes argument temporaries ahead of
    it (`flushes`)."""
    STMT = "stmt"                    # a block's leaf statement
    RAISE = "raise"                  # a raise terminator's statement
    FINALLY_STMT = "finally_stmt"    # a finally helper's statement
    COND = "cond"                    # a branch condition
    RETURN = "return"                # a return terminator
    YIELD_VALUE = "yield_value"      # a generator yield's value
    AWAIT_ARGS = "await_args"        # an inline await's sub-coro arguments
    AWAIT_OPERAND = "await_operand"  # an erased / borrowed await operand
    AWAIT_RECEIVER = "await_receiver"  # a bound-method await's receiver
    WITH_MANAGER = "with_manager"    # a `with` item's manager
    FOR_ITERABLE = "for_iterable"    # a sync loop's source
    ASYNC_FOR_ITERABLE = "async_for_iterable"
    RANGE_BOUND = "range_bound"      # one `range()` bound of a counted loop
    LOOP_STEP = "loop_step"          # a borrow-tuple loop var's lent step
    MATCH_DISPATCH = "match_dispatch"  # a suspending match's dispatch
    NESTED_DEF = "nested_def"        # a nested def's member body

    @property
    def flushes(self) -> bool:
        """The printer flushes pending temporaries ahead of this position's
        line, so its lowering may hoist one there. (A yield value's right
        is its slot's placement, `THIRResumableLeaves.yield_hosts_temps`.)"""
        return self in _FLUSHING_ROLES


_FLUSHING_ROLES = frozenset({
    LeafRole.COND, LeafRole.AWAIT_ARGS, LeafRole.AWAIT_OPERAND,
    LeafRole.FOR_ITERABLE, LeafRole.RANGE_BOUND})


@dataclass(frozen=True, eq=False)
class THIRLeafSlot:
    """A position of the frame where user source renders; its lowering is
    `THIRResumableLeaves.leaves[slot]`. `source` is the parse node lowered
    there (a statement, an expression, the `TpyReturn` / `TpyYield` /
    `TpyMatch` / await operand / `TpyNestedDef` of the position). Compared
    by identity: the frame owns each slot once."""
    role: LeafRole
    source: object

    @property
    def loc(self) -> 'SourceLocation | None':
        return getattr(self.source, "loc", None)


class PtrSlotField(NamedTuple):
    """A frame field the ptr-slot prescan reserved: its name and C++ type,
    and the local its write site re-points at it with the value it
    stores. Published as a PTR_SLOT `THIRFrameHolder`."""
    name: str
    cpp: str
    local: str
    value: 'TpyExpr'


class HolderRole(Enum):
    """What a frame machinery field holds."""
    LOOP = "loop"                  # a loop's source / iterator / sentinel / step / counter
    WITH_CONTEXT = "with_context"  # a `with` item's manager (owned, or a pointer to it)
    PTR_SLOT = "ptr_slot"          # the storage a pointer-form local's write site re-points at
    TRY_FINALLY = "try_finally"    # a decomposed finally's in-flight exception / pending return


@dataclass(frozen=True, eq=False)
class THIRFrameHolder:
    """One field of the frame's own machinery, as decided: its C++ member
    `name` and decided type spelling `cpp_type` (a decision's output, kept a
    string as `THIRArgTemp.cpp_type` is). A leaf or the printer that needs
    the field references this node rather than spelling its name.

    `init` is a TRY_FINALLY slot's declarator initializer; `borrowed` marks
    a WITH_CONTEXT field holding a pointer to a borrowed manager; a
    PTR_SLOT names the `local` its write site re-points at it and the
    `value` it stores. A LOOP holder names the `loop` it belongs to (None
    for an `async for` iterator: whatever `__aiter__` built) and whether
    destroying it may run user code (`drop_runs_user_code`), which decides
    both its release at loop exit and its place in the field order."""
    role: HolderRole
    name: str
    cpp_type: str
    init: str = ""
    borrowed: bool = False
    local: 'str | None' = None
    value: 'TpyExpr | None' = None
    loop: 'FrameLoopOwner | None' = None
    drop_runs_user_code: bool = False


@dataclass(frozen=True, eq=False)
class FrameLoopOwner:
    """The loop a LOOP holder belongs to: the statement, its strategy info
    (None for a loop the CFG keeps whole, whose only field is its source),
    the alias the field is spelled off (None for range counters), and every
    name the loop can bind (`bound_names`: its target and what a pass in its
    body binds -- an over-approximation, since a missed binding is a
    dangling read while a spurious one only costs a lifetime edge)."""
    stmt: TpyForEach
    info: 'GeneratorForInfo | None'
    alias: 'str | None'
    bound_names: frozenset[str]


# -- THIRAwaitSuspension-specific enums (kept here so the frame owns the
# CFG-level metadata; gen_async re-imports them).

class AwaitMode(Enum):
    """How an await transition stores its sub-future in the parent frame.

    INLINE -- statically-known async def; sub-coro struct emplaced as a
        sub-future field.
    ERASED -- value awaitable (Task[T] / user value type implementing
        Awaitable[T]); move-stored in an std::optional sub-future.
    BORROWED -- reference-type awaitable (e.g. Future[T]); stored as a
        raw pointer so observers see the same object across suspensions.
    """
    INLINE = "inline"
    ERASED = "erased"
    BORROWED = "borrowed"


class AwaitKind(Enum):
    """How an await expression's result is consumed at its host stmt."""
    ASSIGN = "assign"
    VARDECL = "vardecl"
    RETURN = "return"
    DISCARD = "discard"


class AsyncWithKind(Enum):
    """Which leg of an `async with` a synthetic yield emits.

    The CFG synthesizes two THIRSuspend BBs per async-with: one for
    `await __cm.__aenter__()` and one for `await __cm.__aexit__(...)`.
    Emit dispatches on this enum rather than re-parsing the AST.
    """
    AENTER = "aenter"
    AEXIT = "aexit"


# -- Suspension payloads (the shape-specific part of a yield terminator)

@dataclass(frozen=True)
class THIRAwaitSuspension:
    """An async-def suspension. Held inside a THIRSuspend terminator."""

    @property
    def source_loc(self) -> 'SourceLocation | None':
        """Where the suspension sits in the source: the await node, else the
        statement that hosts it."""
        if self.await_node.loc is not None:
            return self.await_node.loc
        return self.host_stmt.loc if self.host_stmt is not None else None

    mode: AwaitMode
    sub_field_cpp_type: str       # cpp type for std::optional<...> / pointee
    operand_expr: TpyExpr         # the operand of `await ...`
    kind: AwaitKind
    bind_target: str | None       # variable name for ASSIGN / VARDECL
    return_stmt: TpyReturn | None # the original return for RETURN-kind
    host_stmt: TpyStmt | None     # source-level statement containing the await
    # The TpyAwait node itself (sema attaches awaited_async_func_name /
    # awaited_task_inner here; emit consults them).
    await_node: TpyAwait
    # Prebuilt-slot await: the operand is a bound coroutine handle whose
    # frame local IS the slot -- emit declares no __sub field, skips the
    # emplace, and polls/resets that local's field instead.
    prebuilt_slot: 'THIRFrameLocal | None' = None
    # Async-with internal yields: emit takes a special path that
    # synthesizes `(*__with_ctx_<n>).__aenter__()` or
    # `(*__with_ctx_<n>).__aexit__({}, nullptr, {})` directly rather
    # than rendering a synthesized parse-tree node. Set by
    # `_build_async_with`; None for ordinary user awaits.
    async_with_kind: 'AsyncWithKind | None' = None
    async_with_ctx_n: int | None = None
    # Set by `_build_async_for` so emit can synthesize
    # `__sub_<i>.emplace(*__for_itr_<uid>)` and look up the sub-coro
    # struct name via `func._async_for_struct_names`. None for
    # ordinary user awaits.
    async_for_uid: int | None = None
    # `(name, owner_record | None)` of the SAME-MODULE coro this suspension
    # embeds as a by-value sub-future -- the emit-ordering edge, recorded
    # beside `sub_field_cpp_type` because the same resolution produces both.
    # None when there is nothing to order: a cross-module callee (already
    # complete via its header), or an erased/bound-handle await with no
    # statically-named callee.
    dep_unit: 'tuple[str, str | None] | None' = None
    # Namespace qualifier of a cross-module callee, recorded where the callee
    # is RESOLVED. Struct emit re-spells the sub-coro name when the callee has
    # static-protocol params, and re-deriving the qualifier there would put a
    # second copy of the resolution rules a merge could drift apart.
    sub_struct_module_qual: str | None = None
    # The positions the suspension renders user source at
    # (`await_operand_slots`): an inline call's sub-coro arguments and a
    # bound method's receiver, or an erased / borrowed operand whole. A
    # prebuilt-slot or synthesized (`async with` / `async for`) suspension
    # renders none.
    args: 'THIRLeafSlot | None' = None
    receiver: 'THIRLeafSlot | None' = None
    operand: 'THIRLeafSlot | None' = None


def await_operand_slots(mode: AwaitMode, operand: TpyExpr
                        ) -> 'dict[str, THIRLeafSlot]':
    """The leaf slots of a user await's operand, as keyword fields of its
    `THIRAwaitSuspension`: an inline call renders its arguments (and a
    bound method's receiver, which a module call has none of); an erased /
    borrowed operand renders whole."""
    if mode is not AwaitMode.INLINE:
        return {"operand": THIRLeafSlot(LeafRole.AWAIT_OPERAND, operand)}
    slots = {"args": THIRLeafSlot(LeafRole.AWAIT_ARGS, operand)}
    if (isinstance(operand, TpyMethodCall)
            and operand.user_module_call is None
            and operand.builtin_module_call is None):
        slots["receiver"] = THIRLeafSlot(LeafRole.AWAIT_RECEIVER, operand.obj)
    return slots


@dataclass(frozen=True)
class THIRYieldSuspension:
    """A generator suspension. Held inside a THIRSuspend terminator when the CFG
    is built from a generator body."""

    @property
    def source_loc(self) -> 'SourceLocation | None':
        return self.yield_stmt.loc if self.yield_stmt is not None else None

    value_expr: TpyExpr | None    # the yielded expression (None for bare yield)
    # The source `yield` statement, so emit can reuse the ordinary yield
    # emit (storage->borrow bridging etc.).
    yield_stmt: 'TpyYield | None' = None
    # The yielded value's position (source: the `yield` statement).
    value: 'THIRLeafSlot | None' = None


THIRSuspensionPayload = Union[THIRAwaitSuspension, THIRYieldSuspension]


# -- Regions: try/finally/with frames active at a BB

@dataclass(frozen=True)
class THIRTryRegion:
    """A try/except/finally frame active inside the try body.

    `handlers` are the source-level except handlers; emit wraps the
    case body in `catch (Type& e) { handler.body }` per handler.

    Two finally shapes:
      * Helper-based (`finally_helper_name` set): finally body has no
        awaits and is emitted as a member function `void __finally_<n>()`
        called on every throw / return / fall-through exit.
      * CFG-based (`finally_entry_bb` / `captured_exc_field` set):
        finally body has an await and lives in the main state machine.
        On exception in the try body, the catch-all saves
        `std::current_exception()` to the captured-exc frame field and
        transitions state to `finally_entry_bb`. The finally body ends
        with an `THIRFinallyExit` synthetic stmt that rethrows the
        saved exception (clearing the field) before falling through to
        the post-try BB. Mutually exclusive with the helper-based shape.
    """
    handlers: tuple[TpyExceptHandler, ...]
    finally_helper_name: str | None       # None if try has no finally body OR CFG-based
    # Source-level loc for emit diagnostics.
    loc_source: TpyStmt | None = None
    # CFG-based finally fields (None for helper-based / no finally).
    finally_entry_bb: int | None = None
    captured_exc_field: str | None = None
    # Pending-return slot for CFG-based finally: when set, a `return`
    # inside the try body or any handler stores its value to
    # `pending_return_slot` (when non-None -- void async defs leave it
    # None) and sets `pending_return_flag = true`, then transitions
    # state to the finally entry BB. THIRFinallyExit checks the flag
    # and emits the deferred Poll::ready after the rethrow check.
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class THIRFinallyRegion:
    """Marker for BBs inside a finally body. Emit treats specially:
    a throw inside a finally re-raises *after* the body completes;
    a `return` inside a finally body parks into the same pending-return
    slot as a return from the try / handler body (`finally_exit_bb`
    drains it via THIRFinallyExit, Python: return-in-finally wins and
    swallows any in-flight exception).
    """
    helper_name: str
    loc_source: TpyStmt | None = None
    # Parking fields mirror THIRTryRegion's so a `return` inside the finally
    # body sees the same slot as a return inside the try body. None when
    # the try has no reachable returns anywhere in try / handlers /
    # finally (no slot allocated).
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None
    # Dedicated BB whose single stmt is THIRFinallyExit. Both normal
    # fall-through and a return-in-finally jump here, so THIRFinallyExit
    # is the single replay site.
    finally_exit_bb: int | None = None


@dataclass(frozen=True)
class THIRExceptRegion:
    """Marker for BBs inside an except handler body. The corresponding
    try frame is no longer on the stack (we've already caught the
    exception). The sub-future reset happens in the C++ catch wrapper
    emitted around the resume case body whose suspension threw -- so
    the reset is per-yield, not per-handler.

    `parent_finally` mirrors the enclosing THIRTryRegion's
    `finally_helper_name` so emit can run the finally body when control
    leaves the handler normally (Python semantics: finally runs after
    a matched except).

    `parent_finally_entry_bb` is set instead when the parent try has
    a CFG-based finally: an exception raised inside the handler body
    must transition state to that BB (the inner try/catch in the
    handler emit dispatches this via the THIRTryRegion's
    `captured_exc_field`). A `return` inside the handler body routes
    through the same pending-return slot as a return in the try body.
    """
    handler: TpyExceptHandler
    parent_finally: str | None = None
    loc_source: TpyStmt | None = None
    parent_finally_entry_bb: int | None = None
    parent_pending_return_flag: str | None = None
    parent_pending_return_slot: str | None = None


@dataclass(frozen=True)
class THIRWithRegion:
    """A with frame active inside the body. Emit wraps the case body in
    a try/catch that runs __exit__ with the exception args and either
    suppresses or re-raises depending on __exit__'s return.

    `ctx_n` is the uid used to name both the `__with_ctx_<n>` frame
    slot (where the context-manager object lives, as
    `std::optional<T>`) and the `__exc_<n>` BaseException& binding
    inside the catch wrap. `post_with_bb` is the CFG BB to transition
    to when __exit__ suppresses an exception (only meaningful when
    item.exit_can_suppress).
    """
    item: TpyWithItem
    ctx_n: int
    post_with_bb: int
    loc_source: TpyStmt | None = None


@dataclass(frozen=True)
class THIRLoopSourceRegion:
    """The advance and body of a `for` loop whose source (or the iterator
    or step result over it) the frame holds in fields whose destruction may
    run user code. CPython drops the loop's iterator the moment control
    leaves the loop -- exhaustion, `break`, `return`, an exception -- so a
    source generator's `finally` runs there, not when this frame dies.
    Leaving the region releases `fields` (already in release order).

    It opens no C++ `try`: its cleanup cannot raise (a frame's destructor
    turns a raising `finally` into a panic), so a catch that outlives it
    releases it on entry instead."""
    uid: int
    fields: tuple[str, ...]


THIRRegion = Union[THIRTryRegion, THIRFinallyRegion, THIRExceptRegion, THIRWithRegion,
               THIRLoopSourceRegion]


# -- Terminators: one per BB

@dataclass(frozen=True)
class THIRFall:
    """THIRFall through to next_bb. Used for straight-line edges and
    explicit break/continue (which target loop exit / loop top)."""
    next_bb: int


@dataclass(frozen=True)
class THIRBranch:
    """Conditional branch on `cond`."""
    cond: THIRLeafSlot
    then_bb: int
    else_bb: int


@dataclass(frozen=True)
class THIRSuspend:
    """Suspension point. Saves state, returns Pending (async) / yield
    value (generator); resume_bb is the entry point on resume."""
    payload: THIRSuspensionPayload
    resume_bb: int
    suspension_index: int         # bijective with resume_bb among yields


@dataclass(frozen=True)
class THIRFrameReturn:
    """Return from the function. Emit walks the active finally chain
    before emitting the actual return. `slot`'s leaf is the return's
    `THIRResumableReturn` (value and deferred-capture recipe); the
    `return await` resume block's return has none (the resume step returns
    the polled result itself)."""
    slot: THIRLeafSlot

    @property
    def return_stmt(self) -> TpyReturn:
        return self.slot.source


@dataclass(frozen=True)
class THIRFrameRaise:
    """Raise an exception. Emit walks the active finally chain via
    the exception unwinding mechanism (no explicit chain run)."""
    slot: THIRLeafSlot

    @property
    def raise_stmt(self) -> TpyRaise:
        return self.slot.source


@dataclass(frozen=True)
class THIRFallOff:
    """The body falls off its end: the tail block of the body, which
    completes the frame without a value (a generator is exhausted, a
    `-> None` coroutine is ready). The tail of a body that never reaches
    its end (`while True:` with no `break`) carries it too, in a block
    no edge reaches."""
    pass


@dataclass(frozen=True)
class THIRForAdvance:
    """THIRTerminator for the cond/advance BB of a CFG-decomposed for-loop
    whose body contains await. Emit:
      __for_r_<uid> = (*__for_itr_<uid>).__next__();
      if (!(*__for_r_<uid>).has_value()) -> exhausted_bb
      <loop_var> = ::tpy::unwrap_ref(*(*__for_r_<uid>));
      -> has_value_bb
    Two successors mirror THIRBranch so case-entries pred-counting works.

    `iterable` is the loop setup's source slot (the `next` strategy names
    the source again at each advance; None for a counted `range` loop);
    `step` is the position a borrow-form tuple loop var's lent step renders
    at."""
    uid: int
    stmt: TpyForEach
    has_value_bb: int
    exhausted_bb: int
    iterable: 'THIRLeafSlot | None' = None
    step: 'THIRLeafSlot | None' = None


@dataclass(frozen=True)
class THIRMatchDispatch:
    """THIRTerminator for a `match` whose arm bodies contain a suspension (H1).

    The suspension-free dispatch (subject eval + every arm test + bindings
    + guards) is emitted by reusing the ordinary match dispatch; each arm
    *body* is routed back through the resumable walker (it may suspend),
    so arm bodies live in the state machine while the dispatch keeps its
    type-aware switch / if-elif shape. `arm_bbs[i]` is the entry BB of
    `match_stmt.cases[i].body`; `join_bb` is the post-match continuation
    (None when the match is exhaustive and every arm terminates, so no
    fall-through exists). `slot`'s leaf is the lowered dispatch."""
    slot: THIRLeafSlot
    arm_bbs: tuple[int, ...]
    join_bb: int | None

    @property
    def match_stmt(self) -> TpyMatch:
        return self.slot.source


THIRTerminator = Union[THIRFall, THIRBranch, THIRSuspend, THIRFrameReturn, THIRFrameRaise, THIRFallOff,
                   THIRForAdvance, THIRMatchDispatch]


# -- Synthetic leaf-stmt types for CFG-lowered for-loops ----------------

@dataclass(frozen=True)
class THIRForIterSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed for-loop.

    Sync (`is_async=False`, v1.5 M3.1) emits:
      `::tpy::resumable_iter_init(__for_itr_<uid>, <iterable_expr>);`
    Async (`is_async=True`, v1.5 M6) emits:
      `__for_itr_<uid> = (<iterable_expr>).__aiter__();`
    The frame field name is shared since only one of the two paths is
    active per uid.

    `iterable` is the source's slot (None for a counted `range` loop,
    which renders each of its `bounds` instead); `source` is the iterable
    expression either way."""
    uid: int
    source: TpyExpr
    iterable: 'THIRLeafSlot | None'
    bounds: 'tuple[THIRLeafSlot, ...]' = ()
    is_async: bool = False
    # Loop element type, used by the range strategy's counter init (the
    # emitter needs the C++ element type for the counter/stop casts).
    elem_type: 'TpyType | None' = None
    # Carried from the source loop's `iter_borrow_unplaceable`: sema could
    # file no ITER loan for this iterable, so the lowering refuses it here
    # exactly as the sync for-each route does.
    iter_borrow_unplaceable: bool = False


@dataclass(frozen=True)
class THIRFinallyExit:
    """Synthetic leaf stmt at the single replay site of a CFG-decomposed
    finally region (allocated as the finally's dedicated `finally_exit_bb`
    so both normal fall-through and a return-in-finally drain through one
    point). Two-stage emit -- pending-return check FIRST so a return in
    the finally body wins over any in-flight exception (Python
    semantics):

      if (this-><pending_return_flag>) {
          this-><pending_return_flag> = false;
          this-><captured_exc_field> = nullptr;   // swallow in-flight exc
          <walk outer finally chain>
          __state = S_DONE;
          return Poll<T>::ready(std::move(this-><pending_return_slot>));
      }
      if (this-><captured_exc_field>) {
          std::exception_ptr __tmp = this-><captured_exc_field>;
          this-><captured_exc_field> = nullptr;
          std::rethrow_exception(__tmp);
      }

    Stage 1 fires when a `return` originating in the try body, any
    handler body, OR the finally body itself set the pending flag. It
    has two shapes depending on whether an enclosing CFG-finally region
    is active at this exit site (see `_emit_async_finally_exit`):
      * No enclosing CFG finally: replay locally -- walk the outer
        helper chain, then emit Poll<T>::ready / StopIteration as shown
        above (the deferred return is delivered as the coro's exit).
      * Enclosing CFG finally present: forward -- move this exit's
        parked value into the outer's `pending_return_slot`, set the
        outer's `pending_return_flag`, walk helpers between this exit
        and the outer's finally entry, then transition state to the
        outer's `finally_entry_bb`. The outer's THIRFinallyExit
        eventually performs the local replay (or forwards again, for
        deeper nesting).
    The captured-exc clear-on-its-way-out implements the Python
    return-in-finally-wins-over-raise rule. Stage 2 rethrows a saved
    in-flight exception when no return is pending. Fields are cleared
    after extraction so a re-entry to the same try (e.g. inside a loop)
    doesn't carry over stale state.

    `pending_return_flag` / `pending_return_slot` are None when no
    `return` is reachable in try / handlers / finally (stage 1 omitted).
    `pending_return_slot` is None for void async defs even when the flag
    is set (the deferred return needs no value)."""
    captured_exc_field: str
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class THIRWithEnter:
    """Synthetic leaf stmt at the head of a CFG-decomposed sync `with`
    body (sync `with X:` compiled inside an `async def`, M3.2). Real
    Python `async with` uses its own setup synthetic (see THIRAsyncWithSetup).
    Emit:
      __with_ctx_<n> = <context_expr>;
      (*__with_ctx_<n>).__enter__();     # if target is None
      <target> = (*__with_ctx_<n>).__enter__();   # otherwise
    `manager` is the item's manager expression's slot.
    """
    ctx_n: int
    item: TpyWithItem
    manager: THIRLeafSlot


@dataclass(frozen=True)
class THIRAsyncWithSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed `async with`
    region (v1.5 M5). Stores the context manager in a frame slot so the
    subsequent `__aenter__` / `__aexit__` yields can dispatch through it.

    Emit:
      __with_ctx_<n> = <context_expr>;

    (`__with_ctx_<n>` is the frame-hoisted `std::optional<CM>` field
    allocated by the prescan; assigning a CM rvalue constructs in
    place via `operator=`.) The THIRSuspend site immediately following
    emplaces `__sub_<i>` with `(*__with_ctx_<n>, ...)` for the
    `__aenter__` call; the finally body's THIRSuspend does the same for
    `__aexit__(None, None, None)`.
    """
    ctx_n: int
    item: TpyWithItem
    manager: THIRLeafSlot


THIRBlockStmt = Union[THIRLeafSlot, THIRForIterSetup, THIRWithEnter,
                      THIRAsyncWithSetup, THIRFinallyExit]


# -- Basic block

@dataclass
class THIRBlock:
    """A basic block.

    `stmts` are the block's leaf statements -- each a `THIRLeafSlot`
    (role STMT) whose lowering renders through the leaves -- and the
    frame's pseudo-statements; compound statements that *don't*
    transitively contain a suspension stay as single slots here (lazy
    decomposition). Compound statements that *do* contain suspensions are
    decomposed during CFG construction and never appear in `stmts`.

    `region_stack` is the (outermost ... innermost) list of regions
    active when control enters this BB. Emit wraps the BB's emission
    in the region_stack reconstructed as nested C++ try blocks.

    `region_stack` is mutable (set by the builder's annotation pass).
    """
    id: int
    stmts: 'list[THIRBlockStmt]' = field(default_factory=list)
    terminator: THIRTerminator | None = None
    region_stack: tuple[THIRRegion, ...] = ()
    # Set by the (future) reachability pass; True iff this BB is reached
    # only as the resume entry of a THIRSuspend (i.e. is a state target). Used
    # by emit to decide whether to emit a case label.
    is_resume_entry: bool = False
    # isinstance/`is not None` narrowing facts (var name -> narrowed type)
    # active when control enters this BB. A suspension splits a narrowed
    # region across C++ case scopes, so the narrowed binding (a dynamic_cast
    # / std::get local) does not survive; emit re-establishes it at the
    # resume case from these facts. {} for un-narrowed blocks.
    entry_narrowings: dict[str, "TpyType"] = field(default_factory=dict)


@dataclass
class GeneratorForInfo:
    """Pre-scanned info about a for-loop containing yields in a state machine generator."""
    uid: int
    strategy: str  # "range" | "begin_end" | "next" | "iter_next"
    fields: list[tuple[str, str]]  # (field_name, cpp_type_string) for struct fields
    # Loop variable name when the iter source yields stable lvalue references
    # (begin_end over a NativeIterable container of non-value elements). The
    # variable's frame slot is emitted as `T*` (pointer-form), not
    # `std::optional<T>` (value-copy), so `for it in items: ... prev = it`
    # preserves CPython aliasing semantics across yield/resume instead of
    # copying the element into the frame. None when copy-storage is used.
    pointer_form_loop_var: str | None = None
    # Whether that pointer-form loop var is spelled `const T*`. The advance
    # takes `&(*it++)`, so the pointer's const-ness is the ITERATION SOURCE's:
    # a const-rooted source (readonly container) or a `readonly[E]` element
    # (what sema's `varargs_as_const` flip makes the default for `*args`)
    # both yield a `const T*`, and a plain `T*` field would not compile.
    pointer_form_is_const: bool = False
    # The `using` alias the struct defines for this loop's source type
    # (`__for_src_<uid>_t`); every field above is spelled through it. None
    # for the `range` counters, which have no source object.
    src_alias: str | None = None
    # Pointee spelling per pointer-form field of this loop -- the loop var
    # and the tuple-unpack targets that alias a member -- derived from the
    # SOURCE through `src_alias`, so the pointer's const is the element's.
    pointer_payloads: dict[str, str] = field(default_factory=dict)
    # (loop var name, fully-spelled C++ payload for its frame field):
    # `::tpy::for_elem_next_t<src_alias>`, so C++ decides at instantiation
    # whether the element aliases the source or is owned, with `frame_slot`
    # supplying both forms behind one spelling. Set ONLY by `iter_next`, the
    # one strategy where the choice is open: its source may lend an element
    # or hand back a fresh one. `begin_end` and `next` sources always lend an
    # lvalue, so their loop var is the pointer form above; `range`
    # synthesizes its element; a concrete value element is copy-only.
    loop_var_field: tuple[str, str] | None = None
    # For a tuple-unpack loop (`for a, b in items:`) whose element tuple
    # contains non-value, non-readonly members, the names of the unpack
    # targets bound to those members. They are stored as `T*` (alias into
    # the live container element via the pointer-form `__for_tup`), so
    # mutating an unpacked record propagates to the source -- matching both
    # CPython and the plain pointer-form loop var above. Value/readonly
    # members are NOT listed (they stay value-copy). Empty for non-unpack
    # loops and all-value tuples.
    pointer_form_unpack_targets: frozenset[str] = frozenset()
    # Loop variable name when the element is a pointer-repr `Optional[T]` over
    # a container that lends lvalues (`begin_end`): the frame field is the
    # nullable `T*`, bound at the advance by `optional_to_ptr` of the slot.
    opt_ptr_loop_var: str | None = None
    # Loop variable name when the iterator yields PROXY reference tuples
    # (dict_items: operator* returns std::tuple<const K&, V&> by value).
    # The loop element cannot be address-taken (`&(*it)` is ill-formed on
    # the prvalue proxy), so the loop var's frame slot is the borrow-form
    # tuple (std::tuple<..., T*>) lent element by element; the element
    # refs point into stable node storage, so aliasing across yield/resume
    # still holds. None for non-proxy iterators.
    borrow_tuple_loop_var: str | None = None
    # `(name, owner_record)` of the same-module generator method backing this
    # loop's iteration source, when the frame embeds its struct by value.
    # `iter_next` spells its iterator / result / loop-var fields as
    # `iter_type_t<S>` & friends, which need `S::__iter__`'s RETURN TYPE
    # complete -- another frame struct when that `__iter__` is itself a
    # generator. Recorded here because this is where the source
    # type is resolved; consumed as an emit-ordering edge.
    dep_units: tuple[tuple[str, str | None], ...] = ()
    # Sema's verdict on whether the ITERATION SOURCE is const in this frame (a
    # const-borrow capture, a const-bound local, or a chain rooted at one).
    # The loop's own fields take their const from the source's C++ type; this
    # is for the loop var's BINDING, which the rest of the body reads.
    source_is_const: bool = False


def region_cleanups(region_stack: tuple) -> list:
    """The cleanup actions the regions of this stack run when control
    leaves them -- THE one list every exit reads: a normal edge
    (`_emit_exit_region_finallies`), a `return` (the finally frames
    `_push_finally_helpers` makes of it), an exception (the region
    catches) and abandonment (`_dtor_cleanup_cases`). Each TryRegion /
    ExceptRegion contributes its finally helper name (called via
    `this->name()`); each WithRegion its `__exit__`; each
    LoopSourceRegion the reset of the loop fields it releases. Order:
    outermost first (innermost ends up on top of the stack).

    Each entry is `(kind, payload, region)`: `region` is the source
    region the entry came from, so a consumer can recover the region's
    exit guard from `resumable_region_guards` without a second walk
    (the frame-dtor consumers ignore it)."""
    helpers: list = []
    for region in region_stack:
        if isinstance(region, THIRTryRegion):
            if region.finally_helper_name is not None:
                helpers.append(
                    ("helper", region.finally_helper_name, region))
        elif isinstance(region, THIRExceptRegion):
            if region.parent_finally is not None:
                helpers.append(("helper", region.parent_finally, region))
        elif isinstance(region, THIRWithRegion):
            helpers.append(("with", region, region))
        elif isinstance(region, THIRLoopSourceRegion):
            helpers.append(("reset", region.fields, region))
    return helpers


# -- The published frame

@dataclass(frozen=True)
class THIRFrameLocal:
    """A frame local: a name that lives in the frame across suspensions,
    with its type and its placement verdict (`FrameLocalLayout`). Await
    lifts carry the lift role (`one_shot`): the lift's own declaration
    binds it once from the awaited result."""
    name: str
    type: 'TpyType | None'
    placement: FrameLocalLayout
    one_shot: bool = False


class FrameCaptureKind(Enum):
    """How a frame holds a captured parameter across suspensions (the
    storage-form cascade of `classify_param_kind`)."""
    REF = 0
    VALUE = 1
    POINTER = 2
    TYPE_PARAM = 3
    STATIC_PROTOCOL = 4
    OWNED_VALUE = 5
    FN = 6
    OWNED_COPY = 7
    CAPTURE = 8
    OWNED_TUPLE = 9


@dataclass(eq=False)
class THIRResumableFrame:
    """A generator / coroutine body's frame: its state machine and its
    placement decisions, published where they are decided (the first build:
    the forward-declaration gate for generators, the struct-order sort for
    coroutines, the source-genexpr pass) and read by the struct-order sort,
    struct emit, the factory emitters and the leaf lowering.

    Compared by identity: printer state (case labels, region guards,
    redispatch caches) is keyed by the frame's own objects."""
    func: 'TpyFunction'
    record_name: 'str | None'
    shape: ResumableShape
    # The parameters the frame captures, with their names and types; the
    # leaves (`THIRResumableLeaves.params`) carry them completed with the
    # layout facts the binding table decides. The receiver is not one: it
    # is `receiver`.
    params: 'tuple[THIRParam, ...]'
    # Per parameter, aligned with `params`: how the frame holds it across
    # suspensions.
    captures: 'tuple[FrameCaptureKind, ...]'
    # The parameters whose capture the frame const-qualifies: `readonly[T]`
    # or the inferred const-borrow verdict.
    const_params: frozenset[str]
    # A method frame's `self`: the owning record (a generic record's with
    # its type parameters) at the body's access, read-only for a
    # `@readonly` method; None for a free function.
    receiver: 'THIRBorrowedRecord | None'
    locals: tuple[THIRFrameLocal, ...]
    # -- the coarse CFG
    entry_block: int
    blocks: 'dict[int, THIRBlock]'
    suspensions: 'list[THIRSuspend]'
    # Finally-body helpers emitted as members: (helper name, its
    # statements' slots), in emission order.
    finally_helpers: 'list[tuple[str, tuple[THIRLeafSlot, ...]]]'
    # The source statements and except handlers the builder split into
    # blocks and terminators: none of them lowers as one statement.
    decomposed: IdentitySet
    # Except region -> handler -> the block its body starts in.
    handler_entries: IdentityMap
    # The blocks a case label starts at, in discovery order: the leaf
    # lowering's narrowing boundary; the printer numbers their labels.
    case_entries: tuple[int, ...]
    # The frame's nested defs, emitted as members (role NESTED_DEF; source:
    # the `TpyNestedDef`), in source order.
    nested_defs: 'tuple[THIRLeafSlot, ...]'
    # -- the body the blocks were built over (await-lifted, arguments seated)
    lifted_body: 'list[TpyStmt]'
    # -- loop / with / pointer-slot / try-finally holders, as decided
    for_loop_info: IdentityMap
    for_info_by_uid: 'dict[int, GeneratorForInfo]'
    for_uid_map: IdentityMap
    # The machinery fields, one holder each (`THIRFrameHolder`).
    for_fields: 'tuple[THIRFrameHolder, ...]'
    # Loop -> the LOOP holder of its source, or None where the frame holds
    # none.
    for_src_fields: IdentityMap
    async_for_struct_names: 'dict[int, str]'
    with_uid_map: IdentityMap
    with_fields: 'tuple[THIRFrameHolder, ...]'
    # A non-decomposed `with` -> per item, the WITH_CONTEXT holder of an
    # owned manager the frame homes, or None.
    with_owned_ctx_map: IdentityMap
    async_with_struct_names: 'dict[int, tuple[str, str]]'
    ptr_slot_fields: 'tuple[THIRFrameHolder, ...]'
    # Write site -> {element (None: the scalar) -> its PTR_SLOT holder}.
    ptr_slot_map: IdentityMap
    try_finally_fields: 'tuple[THIRFrameHolder, ...]'
    # Argument lifts: holder name -> (the expression it holds, the local
    # the call binds, if any).
    arg_lifts: 'dict[str, tuple[TpyExpr, str | None]]'
    # Const borrow edges between frame bindings (root -> targets).
    const_source_edges: 'dict[str, list[str]]'
    const_bindings: frozenset[str]
    # `(name, owner_record | None)` of every same-module resumable unit this
    # frame embeds by value: an inline await's sub-future, an `async with` /
    # `async for` dunder's sub-coro, the `__iter__` behind an `iter_next`
    # source, a delegated generator source and a bound frame local.
    embedding_edges: 'tuple[tuple[str, str | None], ...]'
    # Per suspension index, the cleanup actions (`region_cleanups`,
    # innermost first) dropping the frame suspended there runs; suspensions
    # with nothing pending are omitted.
    abandonment_cleanup: 'tuple[tuple[int, tuple], ...]' = ()
    # The case-entry blocks whose body provably cannot throw.
    no_throw_cases: frozenset[int] = frozenset()
    # Some finally helper body returns (the printer's `__finally_stop`).
    finally_helpers_return: bool = False
    eligible: bool = True
    # Recorded at publication: each decomposed loop's setup by uid, and each
    # local by name.
    for_setups: 'dict[int, THIRForIterSetup]' = field(init=False, repr=False)
    _locals_by_name: 'dict[str, THIRFrameLocal]' = field(init=False,
                                                         repr=False)

    def __post_init__(self) -> None:
        self.for_setups = {}
        for block in self.blocks.values():
            for s in block.stmts:
                if isinstance(s, THIRForIterSetup):
                    self.for_setups.setdefault(s.uid, s)
        self._locals_by_name = {loc.name: loc for loc in self.locals}

    def block(self, block_id: int) -> 'THIRBlock':
        return self.blocks[block_id]

    def handler_entry(self, region: 'THIRTryRegion',
                      handler: TpyExceptHandler) -> int:
        return self.handler_entries[region][handler]

    def ptr_slot_field(self, stmt: object,
                       elem: 'int | None' = None) -> 'THIRFrameHolder | None':
        """The holder the pointer-slot prescan reserved for a write site's
        object (`elem`: a pointer tuple's element; None: the scalar), else
        None."""
        return self.ptr_slot_map.get(stmt, {}).get(elem)

    def ptr_tuple_slot_fields(
            self, stmt: object) -> 'dict[int | None, THIRFrameHolder] | None':
        """The holders reserved for a pointer tuple's binding, per owned
        element that takes one; None for a binding never visited."""
        return self.ptr_slot_map.get(stmt)

    def local(self, name: 'str | None') -> 'THIRFrameLocal | None':
        """Frame local `name`, None for a name the frame holds no local of."""
        return self._locals_by_name.get(name) if name is not None else None

    def slot_backed(self, name: 'str | None') -> bool:
        """Whether frame local `name` lives in a `frame_slot` field
        (`SLOT_BACKED_KINDS`): a bind emplaces into it."""
        loc = self.local(name)
        return loc is not None and loc.placement.kind in SLOT_BACKED_KINDS

    def pointer_local(self, name: 'str | None') -> bool:
        """Whether frame local `name` lives in a `T*` field
        (`POINTER_LOCAL_KINDS`)."""
        loc = self.local(name)
        return loc is not None and loc.placement.kind in POINTER_LOCAL_KINDS

    def pointer_with_target(self, name: 'str | None') -> bool:
        """Whether `with ... as` target `name` aims at its enter result
        through a pointer field (`WITH_POINTER_KINDS`)."""
        loc = self.local(name)
        return loc is not None and loc.placement.kind in WITH_POINTER_KINDS

    def for_setup(self, uid: int) -> 'THIRForIterSetup | None':
        """The setup pseudo-statement of the decomposed loop `uid`."""
        return self.for_setups.get(uid)

    def positioned_slots(self):
        """Every leaf slot of the frame with the role its position takes,
        in block order, then the finally helpers and nested defs."""
        for bid in sorted(self.blocks):
            block = self.blocks[bid]
            for s in block.stmts:
                yield from stmt_slots(s)
            yield from terminator_slots(block.terminator)
        for _name, helper in self.finally_helpers:
            for slot in helper:
                yield slot, LeafRole.FINALLY_STMT
        for slot in self.nested_defs:
            yield slot, LeafRole.NESTED_DEF

    def slots(self) -> 'list[THIRLeafSlot]':
        """Every leaf slot of the frame: the positions its leaves may
        fill."""
        return [slot for slot, _role in self.positioned_slots()]


def stmt_slots(s: object) -> 'list[tuple[THIRLeafSlot, LeafRole]]':
    """The leaf slots a block statement holds, with the role each position
    takes."""
    if isinstance(s, THIRLeafSlot):
        return [(s, LeafRole.STMT)]
    if isinstance(s, THIRForIterSetup):
        out = []
        if s.iterable is not None:
            out.append((s.iterable, LeafRole.ASYNC_FOR_ITERABLE if s.is_async
                        else LeafRole.FOR_ITERABLE))
        return out + [(b, LeafRole.RANGE_BOUND) for b in s.bounds]
    if isinstance(s, (THIRWithEnter, THIRAsyncWithSetup)):
        return [(s.manager, LeafRole.WITH_MANAGER)]
    return []


def terminator_slots(t: object) -> 'list[tuple[THIRLeafSlot, LeafRole]]':
    """The leaf slots a terminator holds, with the role each position
    takes. A loop advance names its setup's source slot again, so only its
    step is its own."""
    if isinstance(t, THIRBranch):
        return [(t.cond, LeafRole.COND)]
    if isinstance(t, THIRFrameReturn):
        return [(t.slot, LeafRole.RETURN)]
    if isinstance(t, THIRFrameRaise):
        return [(t.slot, LeafRole.RAISE)]
    if isinstance(t, THIRMatchDispatch):
        return [(t.slot, LeafRole.MATCH_DISPATCH)]
    if isinstance(t, THIRForAdvance):
        return [(t.step, LeafRole.LOOP_STEP)] if t.step is not None else []
    if isinstance(t, THIRSuspend):
        p = t.payload
        if isinstance(p, THIRYieldSuspension):
            return ([(p.value, LeafRole.YIELD_VALUE)]
                    if p.value is not None else [])
        return [(slot, role) for slot, role in (
            (p.receiver, LeafRole.AWAIT_RECEIVER),
            (p.args, LeafRole.AWAIT_ARGS),
            (p.operand, LeafRole.AWAIT_OPERAND)) if slot is not None]
    return []


def terminator_targets(t: object) -> 'tuple[int, ...]':
    """The block ids a terminator transfers to along its CFG edges."""
    if isinstance(t, THIRFall):
        return (t.next_bb,)
    if isinstance(t, THIRBranch):
        return (t.then_bb, t.else_bb)
    if isinstance(t, THIRSuspend):
        return (t.resume_bb,)
    if isinstance(t, THIRForAdvance):
        return (t.has_value_bb, t.exhausted_bb)
    if isinstance(t, THIRMatchDispatch):
        return t.arm_bbs + (() if t.join_bb is None else (t.join_bb,))
    return ()


def frame_ptr_slot_field(frame: 'THIRResumableFrame | None', stmt: object,
                         elem: 'int | None' = None) -> 'THIRFrameHolder | None':
    """`THIRResumableFrame.ptr_slot_field`, None outside a frame."""
    return frame.ptr_slot_field(stmt, elem) if frame is not None else None


def frame_ptr_slot_name(frame: 'THIRResumableFrame | None', stmt: object,
                        elem: 'int | None' = None) -> 'str | None':
    """The C++ member of `frame_ptr_slot_field`'s holder, for a node that
    spells the storage it fills as C++ (a pointer rebind's slot, shared with
    function bodies)."""
    slot = frame_ptr_slot_field(frame, stmt, elem)
    return slot.name if slot is not None else None
