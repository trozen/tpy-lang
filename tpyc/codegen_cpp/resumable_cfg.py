"""Control-flow graph for resumable-frame lowering (async def today; the
future generator migration consumes the same module).

A resumable frame is a state-machine struct whose body method (`__poll__`
for async, `__next__` for generators) dispatches on a state integer to
the right resume point. To support `await` (or `yield`) inside arbitrary
control flow -- if/while/for/try/with -- we lower the function body to
a CFG of basic blocks, then emit each suspension's resume case body
wrapped in the source-level try/except/finally/with stack that was
active at that suspension point. C++'s rule that case labels inside try
blocks aren't reachable from outside the try forces this "replay the
region stack inside each case" pattern; it's what Roslyn's async
rewriter does for the same reason.

This module owns:
- Basic-block + terminator + region data types.
- The CFG builder that walks a resumable (async def / generator) body.
- (Future task) Conservative cross-suspension liveness.
- (Future task) State assignment.

Emission is in `gen_async.py` (and, later, `gen_generators.py`); the
CFG itself is shape-neutral.

Generator migration plan: SuspensionPayload is a tagged union with two
variants -- AwaitPayload (async) and YieldPayload (generator). The
builder dispatches on the node it encounters: a top-level `await` shape
produces an AwaitPayload, a `yield` statement produces a YieldPayload,
and the decomposition predicate (`_stmt_has_any_suspension`) treats both
uniformly. The generator emitter that consumes YieldPayload is the next
migration step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union

from ..parse.nodes import (
    TpyAssign, TpyAwait, TpyBreak, TpyContinue, TpyExceptHandler,
    TpyExpr, TpyExprStmt, TpyForEach, TpyIf, TpyName,
    TpyRaise, TpyReturn, TpyStmt, TpyTry, TpyVarDecl, TpyWhile, TpyWith,
    TpyWithItem, TpyYield,
)


# -- Resumable-frame shape: which state machine the emitter produces.

class ResumableShape(Enum):
    """Which resumable-frame shape the emitter (`gen_async.py`) produces.

    ASYNC -- `async def`: `await` -> `__poll__(Waker) -> Poll<T>`.
    GENERATOR -- generator: `yield` -> `__next__() -> expected<T,
        StopIteration>`.
    """
    ASYNC = "async"
    GENERATOR = "generator"


# -- AwaitPayload-specific enums (kept here so resumable_cfg owns the
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

    M5's CFG synthesizes two Yield BBs per async-with: one for
    `await __cm.__aenter__()` and one for `await __cm.__aexit__(...)`.
    Emit dispatches on this enum rather than re-parsing the AST.
    """
    AENTER = "aenter"
    AEXIT = "aexit"


# -- Suspension payloads (the shape-specific part of a yield terminator)

@dataclass(frozen=True)
class AwaitPayload:
    """An async-def suspension. Held inside a Yield terminator."""
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
    # Async-with internal yields: emit takes a special path that
    # synthesizes `(*__with_ctx_<n>).__aenter__()` or
    # `(*__with_ctx_<n>).__aexit__({}, nullptr, {})` directly rather
    # than going through gen_expr on a synthesized AST. Set by
    # `_build_async_with`; None for ordinary user awaits.
    async_with_kind: 'AsyncWithKind | None' = None
    async_with_ctx_n: int | None = None
    # Set by `_build_async_for` so emit can synthesize
    # `__sub_<i>.emplace(*__for_itr_<uid>)` and look up the sub-coro
    # struct name via `func._async_for_struct_names`. None for
    # ordinary user awaits.
    async_for_uid: int | None = None


@dataclass(frozen=True)
class YieldPayload:
    """A generator suspension. Held inside a Yield terminator when the CFG
    is built from a generator body."""
    value_expr: TpyExpr | None    # the yielded expression (None for bare yield)
    # The source `yield` statement, so emit can reuse the statement
    # generator's `gen_yield_value` (storage->borrow bridging etc.).
    yield_stmt: 'TpyYield | None' = None


SuspensionPayload = Union[AwaitPayload, YieldPayload]


# -- Regions: try/finally/with frames active at a BB

@dataclass(frozen=True)
class TryRegion:
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
        with an `AsyncFinallyExit` synthetic stmt that rethrows the
        saved exception (clearing the field) before falling through to
        the post-try BB. Mutually exclusive with the helper-based shape.
    """
    handlers: tuple[TpyExceptHandler, ...]
    finally_helper_name: str | None       # None if try has no finally body OR CFG-based
    tier: str                              # "throw" / "return" / "finally_only"
    # Source-level loc for emit diagnostics.
    loc_source: TpyStmt | None = None
    # CFG-based finally fields (None for helper-based / no finally).
    finally_entry_bb: int | None = None
    captured_exc_field: str | None = None
    # Pending-return slot for CFG-based finally: when set, a `return`
    # inside the try body or any handler stores its value to
    # `pending_return_slot` (when non-None -- void async defs leave it
    # None) and sets `pending_return_flag = true`, then transitions
    # state to the finally entry BB. AsyncFinallyExit checks the flag
    # and emits the deferred Poll::ready after the rethrow check.
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class FinallyRegion:
    """Marker for BBs inside a finally body. Emit treats specially:
    a throw inside a finally re-raises *after* the body completes;
    a return inside a finally is rejected (TPy async semantics)."""
    helper_name: str
    loc_source: TpyStmt | None = None


@dataclass(frozen=True)
class ExceptRegion:
    """Marker for BBs inside an except handler body. The corresponding
    try frame is no longer on the stack (we've already caught the
    exception). The sub-future reset happens in the C++ catch wrapper
    emitted around the resume case body whose suspension threw -- so
    the reset is per-yield, not per-handler.

    `parent_finally` mirrors the enclosing TryRegion's
    `finally_helper_name` so emit can run the finally body when control
    leaves the handler normally (Python semantics: finally runs after
    a matched except).

    `parent_finally_entry_bb` is set instead when the parent try has
    a CFG-based finally: an exception raised inside the handler body
    must transition state to that BB (the inner try/catch in the
    handler emit dispatches this via the TryRegion's
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
class WithRegion:
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


Region = Union[TryRegion, FinallyRegion, ExceptRegion, WithRegion]


# -- Terminators: one per BB

@dataclass(frozen=True)
class Fall:
    """Fall through to next_bb. Used for straight-line edges and
    explicit break/continue (which target loop exit / loop top)."""
    next_bb: int


@dataclass(frozen=True)
class Branch:
    """Conditional branch on `cond`."""
    cond: TpyExpr
    then_bb: int
    else_bb: int


@dataclass(frozen=True)
class Yield:
    """Suspension point. Saves state, returns Pending (async) / yield
    value (generator); resume_bb is the entry point on resume."""
    payload: SuspensionPayload
    resume_bb: int
    suspension_index: int         # bijective with resume_bb among yields


@dataclass(frozen=True)
class ReturnT:
    """Return from the function. Emit walks the active finally chain
    before emitting the actual return."""
    value: TpyExpr | None
    return_stmt: TpyReturn        # the original statement (for type info)


@dataclass(frozen=True)
class RaiseT:
    """Raise an exception. Emit walks the active finally chain via
    the exception unwinding mechanism (no explicit chain run)."""
    raise_stmt: TpyRaise


@dataclass(frozen=True)
class Unreachable:
    """BB whose end is statically unreachable (e.g. infinite loop body
    with no break/return)."""
    pass


@dataclass(frozen=True)
class AsyncForAdvance:
    """Terminator for the cond/advance BB of a CFG-decomposed for-loop
    whose body contains await. Emit:
      __for_r_<uid> = (*__for_itr_<uid>).__next__();
      if (!(*__for_r_<uid>).has_value()) -> exhausted_bb
      <loop_var> = ::tpy::unwrap_ref(*(*__for_r_<uid>));
      -> has_value_bb
    Two successors mirror Branch so case-entries pred-counting works."""
    uid: int
    stmt: TpyForEach
    has_value_bb: int
    exhausted_bb: int


Terminator = Union[Fall, Branch, Yield, ReturnT, RaiseT, Unreachable,
                   AsyncForAdvance]


# -- Synthetic leaf-stmt types for CFG-lowered for-loops ----------------

@dataclass(frozen=True)
class AsyncForIterSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed for-loop.

    Sync (`is_async=False`, v1.5 M3.1) emits:
      `__for_itr_<uid> = ::tpy::__iter__(<iterable_expr>);`
    Async (`is_async=True`, v1.5 M6) emits:
      `__for_itr_<uid> = (<iterable_expr>).__aiter__();`
    The frame field name is shared since only one of the two paths is
    active per uid."""
    uid: int
    iterable_expr: TpyExpr
    is_async: bool = False
    # Loop element type, used by the range strategy's counter init (the
    # emitter needs the C++ element type for the counter/stop casts).
    elem_type: 'TpyType | None' = None


@dataclass(frozen=True)
class AsyncFinallyExit:
    """Synthetic leaf stmt at the tail of a CFG-decomposed finally body.
    Two-stage emit:

      if (this-><captured_exc_field>) {
          std::exception_ptr __tmp = this-><captured_exc_field>;
          this-><captured_exc_field> = nullptr;
          std::rethrow_exception(__tmp);
      }
      if (this-><pending_return_flag>) {
          this-><pending_return_flag> = false;
          <walk outer finally chain>
          __state = S_DONE;
          return Poll<T>::ready(std::move(this-><pending_return_slot>));
      }

    The first stage rethrows a saved in-flight exception. The
    second stage emits the deferred Poll::ready when a `return` in the
    try/handler body set the pending flag. Fields are cleared
    after extraction so a re-entry to the same try (e.g. inside a
    loop) doesn't carry over stale state.

    `pending_return_flag` / `pending_return_slot` are None when the
    try body / handlers contain no reachable `return` (skip stage 2).
    `pending_return_slot` is None for void async defs even when the
    flag is set (the deferred return needs no value)."""
    captured_exc_field: str
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class WithEnter:
    """Synthetic leaf stmt at the head of a CFG-decomposed sync `with`
    body (sync `with X:` compiled inside an `async def`, M3.2). Real
    Python `async with` uses its own setup synthetic (see AsyncWithSetup).
    Emit:
      __with_ctx_<n> = <context_expr>;
      (*__with_ctx_<n>).__enter__();     # if target is None
      <target> = (*__with_ctx_<n>).__enter__();   # otherwise
    """
    ctx_n: int
    item: TpyWithItem


@dataclass(frozen=True)
class AsyncWithSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed `async with`
    region (v1.5 M5). Stores the context manager in a frame slot so the
    subsequent `__aenter__` / `__aexit__` yields can dispatch through it.

    Emit:
      __with_ctx_<n> = <context_expr>;

    (`__with_ctx_<n>` is the frame-hoisted `std::optional<CM>` field
    allocated by the prescan; assigning a CM rvalue constructs in
    place via `operator=`.) The Yield site immediately following
    emplaces `__sub_<i>` with `(*__with_ctx_<n>, ...)` for the
    `__aenter__` call; the finally body's Yield does the same for
    `__aexit__(None, None, None)`.
    """
    ctx_n: int
    item: TpyWithItem


# -- Basic block

@dataclass
class BB:
    """A basic block.

    `stmts` are leaf statements emitted via the normal StatementGenerator;
    compound statements that *don't* transitively contain a suspension
    stay as single elements here (lazy decomposition). Compound
    statements that *do* contain suspensions are decomposed during CFG
    construction and never appear in `stmts`.

    `region_stack` is the (outermost ... innermost) list of regions
    active when control enters this BB. Emit wraps the BB's emission
    in the region_stack reconstructed as nested C++ try blocks.

    `region_stack` is mutable (set by the builder's annotation pass).
    """
    id: int
    stmts: list[TpyStmt] = field(default_factory=list)
    terminator: Terminator | None = None
    region_stack: tuple[Region, ...] = ()
    # Set by the (future) reachability pass; True iff this BB is reached
    # only as the resume entry of a Yield (i.e. is a state target). Used
    # by emit to decide whether to emit a case label.
    is_resume_entry: bool = False


# -- Loop-context tracking (used during construction; not in the final CFG)

@dataclass
class _LoopCtx:
    """Pushed onto a stack while building loop bodies so break/continue
    can resolve to the correct target BB.

    `continue_bb` is the loop's check/step BB (for while: the condition
    check; for for: the iterator advance). `break_bb` is the BB the
    loop exits to. `regions_at_entry` is the region stack snapshot at
    loop entry, used to compute the region delta on break/continue.
    """
    continue_bb: int
    break_bb: int
    regions_at_entry: tuple[Region, ...]


# -- The CFG container

@dataclass
class CFG:
    entry_bb: int
    blocks: dict[int, BB]
    yield_sites: list[Yield] = field(default_factory=list)
    # Finally-body helper functions to emit as private members. Each
    # entry is (helper_name, body_stmts). Order matters for stable
    # output.
    finally_helpers: list[tuple[str, list[TpyStmt]]] = field(default_factory=list)
    # Cached emit-side maps. Populated lazily by the consumer (e.g.
    # AsyncCoroCodegen) on first use; reused across struct-emit + poll-
    # emit passes so we don't pay the O(N) predecessor walk + region-
    # stack equality scan twice per function. The case-entries value
    # type is consumer-defined (async emits StateLabel; future generator
    # port may use a different shape) -- typed `dict[int, object]` to
    # keep this module shape-neutral.
    _case_entries_cache: dict[int, object] | None = field(default=None, repr=False)
    _resume_to_yield_cache: dict[int, Yield] | None = field(default=None, repr=False)

    def bb(self, bb_id: int) -> BB:
        return self.blocks[bb_id]

    def resume_to_yield(self) -> dict[int, Yield]:
        """`resume_bb -> Yield` map, built once and cached."""
        if self._resume_to_yield_cache is None:
            self._resume_to_yield_cache = {
                y.resume_bb: y for y in self.yield_sites
            }
        return self._resume_to_yield_cache


# -- Builder

class CFGBuilder:
    """Walks a TpyFunction body and produces a CFG.

    Usage:
        builder = CFGBuilder()
        cfg = builder.build(func_body)

    The builder maintains a "current BB" while walking statements.
    Compound statements that contain suspensions branch the CFG;
    statements that don't are appended to the current BB as leaf
    statements.
    """

    def __init__(self, payload_factory=None,
                 for_uid_map: 'dict[int, int] | None' = None,
                 with_uid_map: 'dict[int, list[int]] | None' = None,
                 try_finally_uid_map:
                    'dict[int, int] | None' = None,
                 func_returns_void: bool = False) -> None:
        """
        `payload_factory(await_node, host_stmt, kind, bind_target,
        return_stmt) -> AwaitPayload` is called for each top-level await
        the builder encounters. The factory fills in mode +
        sub_field_cpp_type, which are async-specific. If None, a
        placeholder payload is created (suitable for testing only).

        `for_uid_map` maps id(TpyForEach) -> uid for for-loops that the
        caller has pre-scanned and registered with frame fields. Used by
        `_build_for` to attach the correct uid to AsyncForIterSetup /
        AsyncForAdvance. Missing entries cause `_build_for` to raise
        _CFGNotYetSupported (the caller is expected to pre-register every
        for-with-await loop).

        `with_uid_map` maps id(TpyWith) -> list of ctx_n (one per WithItem,
        in source order) for with-stmts pre-scanned and registered. Used
        by `_build_with`. Missing entries cause `_build_with` to raise.
        """
        self._blocks: dict[int, BB] = {}
        self._next_bb_id: int = 0
        self._yield_sites: list[Yield] = []
        self._finally_helpers: list[tuple[str, list[TpyStmt]]] = []
        self._next_finally_id: int = 0
        # Construction-time stacks.
        self._loop_stack: list[_LoopCtx] = []
        self._region_stack: list[Region] = []
        self._payload_factory = payload_factory
        self._for_uid_map: dict[int, int] = for_uid_map or {}
        self._with_uid_map: dict[int, list[int]] = with_uid_map or {}
        self._try_finally_uid_map: dict[int, int] = try_finally_uid_map or {}
        self._func_returns_void: bool = func_returns_void
        # (id(region), id(handler)) -> handler-entry BB id. Populated
        # by `_record_handler_entry` during try/except construction;
        # read by emitters via `get_handler_entry`.
        self._handler_entries: dict[tuple[int, int], int] = {}

    # -- Public entry point ---------------------------------------------

    def build(self, body: list[TpyStmt]) -> CFG:
        """Build a CFG from a resumable function body (async def or
        generator). Shape-neutral: a `yield` is a suspension point
        alongside `await`."""
        entry = self._new_bb()
        end = self._build_block(entry, body)
        # If the body fell off the end without a terminator, mark the
        # tail BB with Unreachable. The emitter inserts the appropriate
        # "fell-through-without-returning" handling (Ready(unit) for
        # `-> None`, panic otherwise).
        if end is not None and self._blocks[end].terminator is None:
            self._blocks[end].terminator = Unreachable()
        cfg = CFG(
            entry_bb=entry,
            blocks=self._blocks,
            yield_sites=self._yield_sites,
            finally_helpers=self._finally_helpers,
        )
        self._mark_resume_entries(cfg)
        return cfg

    # -- BB helpers -----------------------------------------------------

    def _new_bb(self) -> int:
        bb_id = self._next_bb_id
        self._next_bb_id += 1
        bb = BB(id=bb_id, region_stack=tuple(self._region_stack))
        self._blocks[bb_id] = bb
        return bb_id

    def _finish(self, bb_id: int, terminator: Terminator) -> None:
        """Set the terminator on a BB if it doesn't already have one.
        A BB ending in return/raise/yield/branch is sealed; subsequent
        statements in the source belong to a fresh BB."""
        bb = self._blocks[bb_id]
        if bb.terminator is None:
            bb.terminator = terminator

    def _is_sealed(self, bb_id: int) -> bool:
        return self._blocks[bb_id].terminator is not None

    # -- Body walker ----------------------------------------------------

    def _build_block(self, entry_bb: int, stmts: list[TpyStmt]) -> int | None:
        """Walk a statement list, appending to / forking from entry_bb.
        Returns the BB id where control flows out (None if the block
        terminates unconditionally via return/raise/break/continue)."""
        cur = entry_bb
        for stmt in stmts:
            if self._is_sealed(cur):
                # Source-level dead code after a return/raise/break/
                # continue. Emit into a fresh BB that's only reachable
                # from nothing; it'll be DCE'd by the emitter.
                cur = self._new_bb()
            cur_after = self._build_stmt(cur, stmt)
            if cur_after is None:
                return None
            cur = cur_after
        return cur

    def _build_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Process one statement. Returns the BB id where control flows
        out of this statement (None for unconditional terminators)."""
        # Statements with explicit terminator semantics.
        if isinstance(stmt, TpyReturn):
            return self._build_return(cur, stmt)
        if isinstance(stmt, TpyRaise):
            self._finish(cur, RaiseT(raise_stmt=stmt))
            return None
        if isinstance(stmt, TpyBreak):
            self._build_break(cur)
            return None
        if isinstance(stmt, TpyContinue):
            self._build_continue(cur)
            return None
        # Statements that may host a top-level await.
        if self._stmt_has_top_level_await(stmt):
            return self._build_top_level_await_stmt(cur, stmt)
        # A `yield` is a generator suspension at statement position.
        if isinstance(stmt, TpyYield):
            return self._build_yield_stmt(cur, stmt)
        # A non-loop compound (if/try/with) inside a decomposed loop
        # must be decomposed if it contains break/continue targeting
        # the outer loop -- otherwise the break/continue would become
        # C++ break/continue inside our state-machine switch instead
        # of a CFG state transition.
        force_loop_decomp = (self._loop_stack
                             and _stmt_has_unbound_loop_transfer(stmt))
        # Compound statements: decompose if they contain a nested
        # suspension OR an unbound break/continue inside a loop.
        if isinstance(stmt, TpyIf):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_if(cur, stmt)
        elif isinstance(stmt, TpyWhile):
            if _stmt_has_any_suspension(stmt):
                return self._build_while(cur, stmt)
        elif isinstance(stmt, TpyForEach):
            if stmt.is_async or _stmt_has_any_suspension(stmt):
                return self._build_for(cur, stmt)
        elif isinstance(stmt, TpyTry):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_try(cur, stmt)
        elif isinstance(stmt, TpyWith):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_with(cur, stmt)
        # Leaf statement (or compound without suspensions). If a statement
        # reaches this point STILL containing a suspension, it's a compound
        # kind the builder doesn't decompose (today: `match`). Appending it
        # as a leaf would emit the nested `await`/`yield` as straight-line
        # code -- silently wrong for async, and for a generator it would
        # collide with the resumable state enum. Refuse so the generator
        # trial-build gate routes such functions to the legacy path (and
        # async surfaces a clear error instead of a miscompile).
        if _stmt_has_any_suspension(stmt):
            raise _CFGNotYetSupported(
                "a suspension (`await`/`yield`) inside this statement is "
                "not yet supported by the resumable lowering (the statement "
                "kind is not decomposed by the CFG builder).",
                loc=getattr(stmt, "loc", None),
            )
        self._blocks[cur].stmts.append(stmt)
        return cur

    # -- Top-level await on assign/vardecl/return/expr-stmt -------------

    def _stmt_has_top_level_await(self, stmt: TpyStmt) -> bool:
        return _top_level_await_in(stmt) is not None

    def _build_top_level_await_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Convert a statement whose top-level expression slot is a
        TpyAwait into a Yield terminator. The bind / discard / return
        action runs in the resume BB."""
        await_node = _top_level_await_in(stmt)
        assert await_node is not None
        kind, bind_target, return_stmt = _classify_await_position(stmt, await_node)
        if self._payload_factory is not None:
            payload = self._payload_factory(
                await_node, stmt, kind, bind_target, return_stmt)
        else:
            payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                host_stmt=stmt,
                await_node=await_node,
            )
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = Yield(
            payload=payload,
            resume_bb=resume_bb,
            suspension_index=suspension_idx,
        )
        self._finish(cur, terminator)
        self._yield_sites.append(terminator)
        # If the await's host stmt was a TpyReturn, the resume case
        # body runs the finally chain and returns; control does not
        # flow past it.
        if kind is AwaitKind.RETURN:
            self._finish(resume_bb, ReturnT(value=None, return_stmt=return_stmt))
            return None
        return resume_bb

    def _build_yield_stmt(self, cur: int, stmt: TpyYield) -> int | None:
        """Convert a `yield` statement into a Yield terminator. Unlike an
        `await`, a yield pushes its value OUT to the caller and resumes
        in place -- there is no sub-future to poll and (until `send()`)
        no value bound back in, so the resume BB simply continues after
        the yield. The generator emitter reads `value_expr` / `yield_stmt`
        off the payload to produce the yielded value."""
        payload = YieldPayload(value_expr=stmt.value, yield_stmt=stmt)
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = Yield(
            payload=payload,
            resume_bb=resume_bb,
            suspension_index=suspension_idx,
        )
        self._finish(cur, terminator)
        self._yield_sites.append(terminator)
        return resume_bb

    def _build_return(self, cur: int, stmt: TpyReturn) -> int | None:
        # `return await ...` is the RETURN await kind; routed through
        # _build_top_level_await_stmt.
        if isinstance(stmt.value, TpyAwait):
            return self._build_top_level_await_stmt(cur, stmt)
        self._finish(cur, ReturnT(value=stmt.value, return_stmt=stmt))
        return None

    # -- break / continue -----------------------------------------------

    def _build_break(self, cur: int) -> None:
        if not self._loop_stack:
            # Sema would have rejected; treat as unreachable.
            self._finish(cur, Unreachable())
            return
        target = self._loop_stack[-1].break_bb
        self._finish(cur, Fall(next_bb=target))

    def _build_continue(self, cur: int) -> None:
        if not self._loop_stack:
            self._finish(cur, Unreachable())
            return
        target = self._loop_stack[-1].continue_bb
        self._finish(cur, Fall(next_bb=target))

    # -- if/else --------------------------------------------------------

    def _build_if(self, cur: int, stmt: TpyIf) -> int | None:
        then_bb = self._new_bb()
        else_bb = self._new_bb()
        join_bb = self._new_bb()
        self._finish(cur, Branch(cond=stmt.condition, then_bb=then_bb,
                                  else_bb=else_bb))
        then_end = self._build_block(then_bb, stmt.then_body)
        if then_end is not None:
            self._finish(then_end, Fall(next_bb=join_bb))
        else_end = self._build_block(else_bb, stmt.else_body)
        if else_end is not None:
            self._finish(else_end, Fall(next_bb=join_bb))
        # If both branches terminate, the join is unreachable.
        if then_end is None and else_end is None:
            return None
        return join_bb

    # -- while ----------------------------------------------------------

    def _build_while(self, cur: int, stmt: TpyWhile) -> int | None:
        check_bb = self._new_bb()
        body_bb = self._new_bb()
        # `exit_bb` is the NORMAL-exit target (condition false). When there
        # is an `else` clause it runs there; `break` instead targets a
        # separate `after_bb` that skips the else (Python loop-else
        # semantics). With no else, `after_bb` IS `exit_bb` (no extra BB,
        # so non-else loops are structurally unchanged).
        exit_bb = self._new_bb()
        self._finish(cur, Fall(next_bb=check_bb))
        self._finish(check_bb, Branch(cond=stmt.condition, then_bb=body_bb,
                                       else_bb=exit_bb))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        self._loop_stack.append(_LoopCtx(
            continue_bb=check_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=check_bb))
        finally:
            self._loop_stack.pop()
        if stmt.orelse:
            # else runs on the normal-exit edge, then falls to after_bb;
            # break edges (-> after_bb) skip it. The else body is a regular
            # region so it may itself contain suspensions.
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, Fall(next_bb=after_bb))
            return after_bb
        return exit_bb

    # -- for ------------------------------------------------------------

    def _build_for(self, cur: int, stmt: TpyForEach) -> int | None:
        if stmt.is_async:
            return self._build_async_for(cur, stmt)
        # Universal iter/next lowering. The for-loop becomes:
        #   iter_init -> cond_advance --(has_value)-> body BBs --(fall)-> cond_advance
        #                            --(exhausted)-> exit
        # The cond/advance BB carries an AsyncForAdvance terminator
        # which expands at emit to next() + has_value check + bind.
        uid = self._for_uid_map.get(id(stmt))
        if uid is None:
            raise _CFGNotYetSupported(
                "for-loop with await reached CFG builder without a "
                "registered uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        iter_init_bb = self._new_bb()
        cond_bb = self._new_bb()
        body_bb = self._new_bb()
        # `exit_bb` is the EXHAUSTED (normal-exit) target; the `else` clause
        # runs there. `break` targets `after_bb` (skips else). No else ->
        # after_bb IS exit_bb (non-else for-loops unchanged).
        exit_bb = self._new_bb()
        # Fall into iter init.
        self._finish(cur, Fall(next_bb=iter_init_bb))
        # iter_init_bb: setup, fall to cond.
        self._blocks[iter_init_bb].stmts.append(
            AsyncForIterSetup(uid=uid, iterable_expr=stmt.iterable,
                              elem_type=stmt.elem_type))
        self._finish(iter_init_bb, Fall(next_bb=cond_bb))
        # cond_bb: AsyncForAdvance terminator (advance + branch).
        self._finish(cond_bb, AsyncForAdvance(
            uid=uid, stmt=stmt,
            has_value_bb=body_bb, exhausted_bb=exit_bb))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        # body: continue=cond_bb, break=after_bb (skips the else).
        self._loop_stack.append(_LoopCtx(
            continue_bb=cond_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()
        if stmt.orelse:
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, Fall(next_bb=after_bb))
            return after_bb
        return exit_bb

    def _build_async_for(self, cur: int, stmt: TpyForEach) -> int | None:
        """Lower `async for y in ait: <body>` (v1.5 M6).

        Shape:
          iter_init_bb (AsyncForIterSetup(is_async=True))
            -> TryRegion[ExceptRegion(StopAsyncIteration -> break)] {
                 cond_bb: Yield(await __aiter.__anext__(), bind y)
                 resume_bb: (binds y, falls out of try)
               }
            (pop TryRegion)
            body_bb: <user body>, Fall -> cond_bb
            handler -> exit_bb (via the loop's break_bb)

        The TryRegion wraps ONLY the cond/resume pair, NOT the body.
        A StopAsyncIteration from the body must propagate up like any
        other exception, so the body's case bodies must not be wrapped
        in the auto-catch.

        Parser rejects `else:` on async-for, so no orelse handling here.
        """
        uid = self._for_uid_map.get(id(stmt))
        if uid is None:
            raise _CFGNotYetSupported(
                "async-for reached CFG builder without a registered "
                "uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        # body_bb / exit_bb are created OUTSIDE the TryRegion push so a
        # StopAsyncIteration thrown from the body propagates rather than
        # being silently caught by the loop's auto-handler. iter_init_bb
        # likewise stays outside (the __aiter__ call shouldn't throw
        # StopAsyncIteration, and a try region around it would distort
        # the case structure). cond_bb / resume_bb / handler body BB are
        # created below INSIDE the push so the emitter wraps their case
        # bodies in `catch (StopAsyncIteration&)`.
        iter_init_bb = self._new_bb()
        body_bb = self._new_bb()
        exit_bb = self._new_bb()

        self._finish(cur, Fall(next_bb=iter_init_bb))
        self._blocks[iter_init_bb].stmts.append(
            AsyncForIterSetup(uid=uid, iterable_expr=stmt.iterable,
                              is_async=True))

        # The handler body is a synthesized TpyBreak -- _build_block
        # routes it through _loop_stack to the loop's break_bb, so
        # _loop_stack must be active while the handler is built.
        handler = TpyExceptHandler(
            exception_type="StopAsyncIteration",
            binding=None,
            body=[TpyBreak(loc=stmt.loc)],
            loc=stmt.loc,
        )
        try_region = TryRegion(
            handlers=(handler,),
            finally_helper_name=None,
            tier="throw",
            loc_source=stmt,
        )

        self._region_stack.append(try_region)
        cond_bb = self._new_bb()
        resume_bb = self._new_bb()
        self._loop_stack.append(_LoopCtx(
            continue_bb=cond_bb,
            break_bb=exit_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            self._finish(iter_init_bb, Fall(next_bb=cond_bb))
            dummy_await = TpyAwait(value=stmt.iterable, loc=stmt.loc)
            payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",  # filled in by struct emit
                operand_expr=stmt.iterable,
                kind=AwaitKind.ASSIGN,
                bind_target=stmt.var,
                return_stmt=None,
                host_stmt=stmt,
                await_node=dummy_await,
                async_for_uid=uid,
            )
            yield_term = Yield(
                payload=payload,
                resume_bb=resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(cond_bb, yield_term)
            self._yield_sites.append(yield_term)
            # Build the synthesized except handler body (single TpyBreak)
            # while the TryRegion is still on the stack so the
            # ExceptRegion has the right parent.
            except_region = ExceptRegion(
                handler=handler,
                parent_finally=None,
                loc_source=stmt,
            )
            self._region_stack.append(except_region)
            try:
                handler_entry = self._new_bb()
                handler_end = self._build_block(handler_entry, handler.body)
                assert handler_end is None  # TpyBreak terminates
                self._record_handler_entry(try_region, handler, handler_entry)
            finally:
                self._region_stack.pop()
        finally:
            self._region_stack.pop()

        # body_bb sits outside the TryRegion -- a user `await` in the
        # body whose result throws StopAsyncIteration propagates up
        # rather than being silently swallowed.
        self._finish(resume_bb, Fall(next_bb=body_bb))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()

        return exit_bb

    # -- try/except/finally ---------------------------------------------

    def _build_try(self, cur: int, stmt: TpyTry) -> int | None:
        finally_name: str | None = None
        finally_entry_bb: int | None = None
        captured_exc_field: str | None = None
        pending_return_flag: str | None = None
        pending_return_slot: str | None = None
        finally_async = bool(stmt.finally_body) and _stmts_have_any_suspension(
            stmt.finally_body)
        if stmt.finally_body and not finally_async:
            finally_name = f"__finally_{self._next_finally_id}"
            self._next_finally_id += 1
            self._finally_helpers.append((finally_name, list(stmt.finally_body)))
        elif finally_async:
            # CFG-based finally. Pre-scan
            # assigns an exception-slot uid and (if returns are present
            # anywhere reachable) a pending-return slot. Remaining
            # restrictions: no return inside the finally body itself
            # (would need override semantics), and no nesting of two
            # CFG-based finally regions (the inner AsyncFinallyExit
            # would need to know about the outer slot for pending-
            # return forwarding).
            for r in self._region_stack:
                if (isinstance(r, TryRegion)
                        and r.captured_exc_field is not None):
                    raise _CFGNotYetSupported(
                        "nesting two `await`-in-`finally` regions is "
                        "a planned follow-up.",
                        loc=stmt.loc,
                    )
            uid = self._try_finally_uid_map.get(id(stmt))
            if uid is None:
                raise _CFGNotYetSupported(
                    "try-finally-with-await reached CFG builder "
                    "without a registered exception-slot uid "
                    "(internal: pre-scan missed it).",
                    loc=stmt.loc,
                )
            captured_exc_field = f"__finally_exc_{uid}"
            if _stmts_have_any_return(stmt.finally_body):
                raise _CFGNotYetSupported(
                    "`return` inside a finally body that itself "
                    "contains await is a planned follow-up.",
                    loc=stmt.loc,
                )
            # Pending-return slot: allocated only when there's a
            # reachable `return` inside try-body or any handler-body.
            # `pending_return_slot` is None for void async defs (the
            # flag alone suffices).
            try_has_return = (_stmts_have_any_return(stmt.try_body)
                               or any(_stmts_have_any_return(h.body)
                                       for h in stmt.handlers))
            if try_has_return:
                pending_return_flag = f"__finally_pending_{uid}"
                # Void async defs need only the flag (no value to park);
                # leave `pending_return_slot` None so emit sites can
                # consult one source of truth (the slot name) instead
                # of also re-deriving void-ness.
                if self._func_returns_void:
                    pending_return_slot = None
                else:
                    pending_return_slot = f"__finally_ret_{uid}"
            else:
                pending_return_flag = None
                pending_return_slot = None

        # Pre-allocate finally_entry_bb (CFG-based finally only) so
        # TryRegion can carry it. The BB must capture FinallyRegion on
        # its region_stack -- temporarily push/pop the finally_region.
        finally_region: FinallyRegion | None = None
        if finally_async:
            assert captured_exc_field is not None
            finally_region = FinallyRegion(
                helper_name=captured_exc_field,
                loc_source=stmt,
            )
            self._region_stack.append(finally_region)
            finally_entry_bb = self._new_bb()
            self._region_stack.pop()

        try_region = TryRegion(
            handlers=tuple(stmt.handlers),
            finally_helper_name=finally_name,
            tier=stmt.tier or "throw",
            loc_source=stmt,
            finally_entry_bb=finally_entry_bb,
            captured_exc_field=captured_exc_field,
            pending_return_flag=pending_return_flag,
            pending_return_slot=pending_return_slot,
        )
        # join_bb is outside the try frame.
        join_bb = self._new_bb()

        # Target for normal try-body exit: finally_entry when CFG-based
        # finally (so finally body runs before reaching join); join
        # otherwise.
        normal_exit_target = (finally_entry_bb if finally_async
                              else join_bb)

        # Try body BBs live inside the TryRegion. Push the region BEFORE
        # creating them so their region_stack captures correctly.
        self._region_stack.append(try_region)
        try:
            try_body_bb = self._new_bb()
            self._finish(cur, Fall(next_bb=try_body_bb))
            try_end = self._build_block(try_body_bb, stmt.try_body)
            if try_end is not None:
                if stmt.else_body:
                    else_end = self._build_block(try_end, stmt.else_body)
                    if else_end is not None:
                        self._finish(else_end,
                                      Fall(next_bb=normal_exit_target))
                else:
                    self._finish(try_end,
                                  Fall(next_bb=normal_exit_target))
        finally:
            self._region_stack.pop()

        # Except-handler bodies: the corresponding TryRegion is no
        # longer active inside the handler (the throw has been caught);
        # the ExceptRegion takes its place so emit knows we're in a
        # handler body (for sub-future reset, exception-binding scope).
        # When the parent try has a CFG-based finally, the
        # ExceptRegion also carries the parent's captured_exc /
        # finally_entry so emit can route raises and returns inside
        # the handler body through the finally region.
        for handler in stmt.handlers:
            except_region = ExceptRegion(
                handler=handler,
                parent_finally=try_region.finally_helper_name,
                parent_finally_entry_bb=try_region.finally_entry_bb,
                parent_pending_return_flag=try_region.pending_return_flag,
                parent_pending_return_slot=try_region.pending_return_slot,
                loc_source=stmt,
            )
            self._region_stack.append(except_region)
            try:
                handler_entry = self._new_bb()
                handler_end = self._build_block(handler_entry, handler.body)
                if handler_end is not None:
                    # Handler's normal exit: when finally is CFG-based,
                    # route through finally entry so the finally body
                    # runs before reaching the post-try join.
                    self._finish(handler_end,
                                  Fall(next_bb=normal_exit_target))
                self._record_handler_entry(try_region, handler, handler_entry)
            finally:
                self._region_stack.pop()

        # Finally body (CFG-based path).
        if finally_async:
            assert finally_region is not None
            assert finally_entry_bb is not None
            assert captured_exc_field is not None
            self._region_stack.append(finally_region)
            try:
                finally_end = self._build_block(
                    finally_entry_bb, stmt.finally_body)
                if finally_end is not None:
                    # Append the AsyncFinallyExit stmt (rethrow check
                    # + pending-return check) then fall to join.
                    self._blocks[finally_end].stmts.append(
                        AsyncFinallyExit(
                            captured_exc_field=captured_exc_field,
                            pending_return_flag=pending_return_flag,
                            pending_return_slot=pending_return_slot,
                        ))
                    self._finish(finally_end, Fall(next_bb=join_bb))
            finally:
                self._region_stack.pop()

        return join_bb

    def _record_handler_entry(self, region: TryRegion,
                               handler: TpyExceptHandler,
                               entry_bb: int) -> None:
        # Stash on a per-builder side map keyed by (id(region), id(handler))
        # so the emit can find handler entries without mutating the
        # frozen region dataclass.
        self._handler_entries[(id(region), id(handler))] = entry_bb

    def get_handler_entry(self, region: TryRegion,
                           handler: TpyExceptHandler) -> int | None:
        return self._handler_entries.get((id(region), id(handler)))

    # -- with -----------------------------------------------------------

    def _build_with(self, cur: int, stmt: TpyWith) -> int | None:
        """Lower a sync `with X as t: body` whose body contains await
        into a CFG region. The context manager lives in a frame-hoisted
        field (`__with_ctx_<n>`); the case-emit wraps each case body in
        try/catch that runs `__exit__` on exception (with optional
        suppression -> transition to post-with state).

        Async `with` (`stmt.is_async`) routes to a separate path: the
        `__aenter__` and `__aexit__` calls are themselves suspensions
        and need Yield BBs, modelled via a synthetic TryRegion whose
        CFG-based finally body holds the `__aexit__` yield. See
        `_build_async_with`."""
        if stmt.is_async:
            return self._build_async_with(cur, stmt)
        ctx_ns = self._with_uid_map.get(id(stmt))
        if ctx_ns is None:
            raise _CFGNotYetSupported(
                "with-stmt with await reached CFG builder without "
                "registered ctx uids (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        if len(ctx_ns) != len(stmt.items):
            raise _CFGNotYetSupported(
                "with-stmt ctx uid count mismatch (internal).",
                loc=stmt.loc,
            )
        post_with_bb = self._new_bb()
        # For each item, push WithRegion then emit WithEnter into
        # the current BB. Items are processed in source order
        # (outer-most CM enters first).
        regions: list[WithRegion] = []
        for item, ctx_n in zip(stmt.items, ctx_ns):
            region = WithRegion(
                item=item,
                ctx_n=ctx_n,
                post_with_bb=post_with_bb,
                loc_source=stmt,
            )
            self._blocks[cur].stmts.append(
                WithEnter(ctx_n=ctx_n, item=item))
            self._region_stack.append(region)
            regions.append(region)
        try:
            # Body BBs live inside all WithRegions.
            body_entry = self._new_bb()
            self._finish(cur, Fall(next_bb=body_entry))
            body_end = self._build_block(body_entry, stmt.body)
            if body_end is not None:
                # Fall out -> exit BB. Region exit (normal __exit__) is
                # emitted via `_emit_exit_region_finallies` when the
                # transition crosses out of the With regions.
                self._finish(body_end, Fall(next_bb=post_with_bb))
        finally:
            # Pop regions in reverse so the region_stack is clean
            # outside this with.
            for _ in regions:
                self._region_stack.pop()
        return post_with_bb

    # -- async with -----------------------------------------------------

    def _build_async_with(self, cur: int, stmt: TpyWith) -> int | None:
        """Lower Python `async with X1 as a, X2 as b: body` (v1.5 M5).

        Each item is decomposed left-to-right into:
          AsyncWithSetup -> Yield(__aenter__) -> [TryRegion {
              <recurse to next item, or body>
          } finally {
              Yield(__aexit__) ; AsyncFinallyExit
          }]

        The TryRegion's finally body re-uses the M3.3 CFG-based-finally
        machinery: a `__finally_exc_<uid>` frame slot saves any in-flight
        exception via `std::current_exception()`; AsyncFinallyExit at
        the tail rethrows. `return` inside the body parks the value in
        `__finally_pending_<uid>` / `__finally_ret_<uid>` and walks
        through the same finally path (same as M3.3.2).

        v1.5 M5 cleanup-only: `__aexit__` is called with all-None args
        regardless of whether an exception was caught. Inspecting
        `exc_val: Optional[BaseException]` is deferred to the
        polymorphic-exception-storage milestone (E9).
        """
        ctx_ns = self._with_uid_map.get(id(stmt))
        if ctx_ns is None:
            raise _CFGNotYetSupported(
                "async-with reached CFG builder without registered "
                "ctx uids (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        if len(ctx_ns) != len(stmt.items):
            raise _CFGNotYetSupported(
                "async-with ctx uid count mismatch (internal).",
                loc=stmt.loc,
            )
        finally_uid = self._try_finally_uid_map.get(id(stmt))
        if finally_uid is None:
            raise _CFGNotYetSupported(
                "async-with reached CFG builder without registered "
                "finally-exc uid (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        # Nested async-with: each item gets its own TryRegion, but the
        # M3.3 finally machinery only supports one CFG-based finally at
        # a time (filed in BUGS.md). Reject multi-item async-with for
        # now -- left-to-right desugaring via the same uid mechanism
        # would collide. (Parser also supports the workaround:
        # nested async-with stmts.)
        if len(stmt.items) != 1:
            raise _CFGNotYetSupported(
                "multi-item `async with X as a, Y as b:` is not yet "
                "supported -- nest two `async with` statements instead",
                loc=stmt.loc,
            )
        # Outer TryRegion can't be inside another CFG-based finally
        # region (same M3.3 nesting limit).
        for r in self._region_stack:
            if (isinstance(r, TryRegion)
                    and r.captured_exc_field is not None):
                raise _CFGNotYetSupported(
                    "`async with` nested inside another `await`-in-"
                    "finally region is a planned follow-up.",
                    loc=stmt.loc,
                )

        item = stmt.items[0]
        ctx_n = ctx_ns[0]
        captured_exc_field = f"__finally_exc_{finally_uid}"

        # 1. AsyncWithSetup populates the __with_ctx_<n> frame slot.
        self._blocks[cur].stmts.append(
            AsyncWithSetup(ctx_n=ctx_n, item=item))

        # 2. Yield for `__aenter__()`. Synthesize a minimal TpyAwait so
        # the payload's dataclass invariants hold; emit branches on
        # `async_with_kind` and never reads the AST. The sub-coro
        # struct's C++ name is resolved at codegen time by looking up
        # `_async_with_struct_names[ctx_n]` (populated by the prescan).
        dummy_await = TpyAwait(value=item.context_expr, loc=stmt.loc)
        aenter_resume_bb = self._new_bb()
        aenter_payload = AwaitPayload(
            mode=AwaitMode.INLINE,
            sub_field_cpp_type="",  # emit fills this from the CM type
            operand_expr=item.context_expr,
            kind=(AwaitKind.ASSIGN if item.target is not None
                  else AwaitKind.DISCARD),
            bind_target=item.target,
            return_stmt=None,
            host_stmt=stmt,
            await_node=dummy_await,
            async_with_kind=AsyncWithKind.AENTER,
            async_with_ctx_n=ctx_n,
        )
        aenter_yield = Yield(
            payload=aenter_payload,
            resume_bb=aenter_resume_bb,
            suspension_index=len(self._yield_sites),
        )
        self._finish(cur, aenter_yield)
        self._yield_sites.append(aenter_yield)

        # 3. Build TryRegion with CFG-based finally. The body is
        # stmt.body; the finally body holds the __aexit__ Yield +
        # AsyncFinallyExit.
        pending_return_flag = None
        pending_return_slot = None
        if _stmts_have_any_return(stmt.body):
            pending_return_flag = f"__finally_pending_{finally_uid}"
            if not self._func_returns_void:
                pending_return_slot = f"__finally_ret_{finally_uid}"

        # Build finally body BB (with FinallyRegion on stack).
        finally_region = FinallyRegion(
            helper_name=captured_exc_field,
            loc_source=stmt,
        )
        self._region_stack.append(finally_region)
        finally_entry_bb = self._new_bb()
        self._region_stack.pop()

        try_region = TryRegion(
            handlers=(),
            finally_helper_name=None,
            tier="throw",
            loc_source=stmt,
            finally_entry_bb=finally_entry_bb,
            captured_exc_field=captured_exc_field,
            pending_return_flag=pending_return_flag,
            pending_return_slot=pending_return_slot,
        )
        join_bb = self._new_bb()

        # Try body lives inside TryRegion.
        self._region_stack.append(try_region)
        try:
            try_body_entry = self._new_bb()
            self._finish(aenter_resume_bb, Fall(next_bb=try_body_entry))
            body_end = self._build_block(try_body_entry, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=finally_entry_bb))
        finally:
            self._region_stack.pop()

        # Finally body: Yield for __aexit__ + AsyncFinallyExit + Fall
        # to join.
        self._region_stack.append(finally_region)
        try:
            dummy_aexit_await = TpyAwait(
                value=item.context_expr, loc=stmt.loc)
            aexit_resume_bb = self._new_bb()
            aexit_payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",
                operand_expr=item.context_expr,
                kind=AwaitKind.DISCARD,
                bind_target=None,
                return_stmt=None,
                host_stmt=stmt,
                await_node=dummy_aexit_await,
                async_with_kind=AsyncWithKind.AEXIT,
                async_with_ctx_n=ctx_n,
            )
            aexit_yield = Yield(
                payload=aexit_payload,
                resume_bb=aexit_resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(finally_entry_bb, aexit_yield)
            self._yield_sites.append(aexit_yield)
            # After aexit resume: AsyncFinallyExit (rethrow saved exc if
            # any, deferred return if pending), then Fall to join.
            self._blocks[aexit_resume_bb].stmts.append(
                AsyncFinallyExit(
                    captured_exc_field=captured_exc_field,
                    pending_return_flag=pending_return_flag,
                    pending_return_slot=pending_return_slot,
                ))
            self._finish(aexit_resume_bb, Fall(next_bb=join_bb))
        finally:
            self._region_stack.pop()

        return join_bb

    # -- post-construction passes ---------------------------------------

    def _mark_resume_entries(self, cfg: CFG) -> None:
        for y in cfg.yield_sites:
            cfg.blocks[y.resume_bb].is_resume_entry = True


# -- Errors ---------------------------------------------------------------

class _CFGNotYetSupported(Exception):
    """Raised when the CFG builder hits a shape that's known but not yet
    implemented. Caller catches and converts to a CodeGenError with the
    appropriate location.
    """
    def __init__(self, msg: str, loc) -> None:
        super().__init__(msg)
        self.msg = msg
        self.loc = loc


# -- Helpers (top-level, used by gen_async too) ---------------------------

def _top_level_await_in(stmt: TpyStmt) -> TpyAwait | None:
    """Return the TpyAwait if `stmt` is one of the top-level
    statement-position await shapes: assign with await RHS, vardecl
    with await init, return with await value, or expression-statement
    with bare await. Returns None otherwise.
    """
    value: TpyExpr | None
    if isinstance(stmt, TpyAssign):
        value = stmt.value
    elif isinstance(stmt, TpyVarDecl):
        value = stmt.init
    elif isinstance(stmt, TpyReturn):
        value = stmt.value
    elif isinstance(stmt, TpyExprStmt):
        value = stmt.expr
    else:
        value = None
    if isinstance(value, TpyAwait):
        return value
    return None


def _classify_await_position(
    stmt: TpyStmt, await_node: TpyAwait,
) -> tuple[AwaitKind, str | None, TpyReturn | None]:
    """Return (kind, bind_target, return_stmt) for a top-level await."""
    if isinstance(stmt, TpyAssign) and stmt.value is await_node:
        target = stmt.target
        if not isinstance(target, TpyName):
            raise _CFGNotYetSupported(
                "await result can only be bound to a simple name in v1; "
                "field/index targets are not yet supported.",
                loc=stmt.loc,
            )
        return (AwaitKind.ASSIGN, target.name, None)
    if isinstance(stmt, TpyVarDecl) and stmt.init is await_node:
        return (AwaitKind.VARDECL, stmt.name, None)
    if isinstance(stmt, TpyReturn) and stmt.value is await_node:
        return (AwaitKind.RETURN, None, stmt)
    if isinstance(stmt, TpyExprStmt) and stmt.expr is await_node:
        return (AwaitKind.DISCARD, None, None)
    raise _CFGNotYetSupported(
        "internal: _classify_await_position called on unsupported stmt.",
        loc=stmt.loc,
    )


def _stmt_has_any_suspension(stmt: TpyStmt) -> bool:
    """Walk a statement (its expression slots and sub_bodies) for any
    suspension point -- a `TpyAwait` (async) or a `TpyYield` (generator).
    Used for the lazy-decomposition decision: a compound statement is
    only lowered to a CFG region if it contains a suspension. `async
    with` and `async for` always count even when their bodies have none
    -- the `__aenter__` / `__aexit__` / `__anext__` calls are themselves
    suspensions."""
    if isinstance(stmt, TpyYield):
        return True
    if isinstance(stmt, TpyWith) and stmt.is_async:
        return True
    if isinstance(stmt, TpyForEach) and stmt.is_async:
        return True

    def walk_expr(e: TpyExpr | None) -> bool:
        if e is None:
            return False
        if isinstance(e, TpyAwait):
            return True
        for c in (e.children() if hasattr(e, "children") else ()):
            if walk_expr(c):
                return True
        return False

    if hasattr(stmt, "exprs"):
        for e in stmt.exprs():
            if walk_expr(e):
                return True
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                if _stmt_has_any_suspension(s):
                    return True
    return False


def _stmts_have_any_suspension(stmts: list[TpyStmt]) -> bool:
    return any(_stmt_has_any_suspension(s) for s in stmts)


def _stmts_have_suspending_compound(
        stmts: list[TpyStmt], kinds: tuple[type, ...]) -> bool:
    """True if any statement in `stmts` (recursively) whose type is in
    `kinds` carries a suspension -- i.e. it contains an `await`/`yield` or
    is an `async for`/`async with`. The generator eligibility gate uses
    this to defer compound kinds whose resumable lowering isn't ready yet:
    `for` loops (the CFG's universal iter/next lowering still needs to be
    reconciled with the legacy range/begin-end peepholes and tuple-unpack
    frame fields) and `try`/`with` (the exception/finally emit path is
    still await-specific -- it resets sub-futures and reads `payload.mode`,
    neither of which a generator yield site carries). A statement of one of
    these kinds with no suspension is plain leaf code and does not count."""
    for s in stmts:
        if isinstance(s, kinds) and _stmt_has_any_suspension(s):
            return True
        if hasattr(s, "sub_bodies"):
            for body in s.sub_bodies():
                if _stmts_have_suspending_compound(body, kinds):
                    return True
    return False


def _stmts_have_tuple_unpack_for_with_suspension(stmts: list[TpyStmt]) -> bool:
    """True if any `for a, b in ...` (tuple-unpack) loop in `stmts`
    (recursively) carries a suspension. The resumable for-loop emit binds
    only the synthetic `__for_tup_<n>` loop var into the frame; the
    destructured targets (`a`, `b`) are emitted as ordinary unpack
    statements, so a target read across a `yield`/`await` does not persist
    (it reads a never-assigned frame field -> garbage). The generator gate
    defers these to the legacy path -- which binds the targets as frame-
    field assignments -- until the resumable path emits the unpack targets
    into the frame too."""
    for s in stmts:
        if (isinstance(s, TpyForEach) and s.is_tuple_unpack
                and _stmt_has_any_suspension(s)):
            return True
        if hasattr(s, "sub_bodies"):
            for body in s.sub_bodies():
                if _stmts_have_tuple_unpack_for_with_suspension(body):
                    return True
    return False


def _stmts_have_any_return(stmts: list[TpyStmt]) -> bool:
    """True if any of `stmts` (recursively through sub_bodies) contains
    a TpyReturn. Drives the pending-return-slot allocation decision
    for a CFG-based finally region."""
    for s in stmts:
        if isinstance(s, TpyReturn):
            return True
        if hasattr(s, "sub_bodies"):
            for b in s.sub_bodies():
                if _stmts_have_any_return(b):
                    return True
    return False


def _stmt_has_unbound_loop_transfer(stmt: TpyStmt) -> bool:
    """True if `stmt` directly or transitively contains a break/continue
    that targets the enclosing loop (not bound by an inner loop in this
    statement). Used to decide whether a leaf compound (if/try/with)
    inside a CFG-decomposed loop needs decomposition so its break /
    continue translate to state transitions.

    Stops at nested loops (while/for): their own break/continue bind
    to them and stay as C++ break/continue (the nested loop is a
    leaf compound)."""
    if isinstance(stmt, (TpyBreak, TpyContinue)):
        return True
    # Nested loop swallows its own break/continue.
    if isinstance(stmt, (TpyWhile, TpyForEach)):
        return False
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                if _stmt_has_unbound_loop_transfer(s):
                    return True
    return False


