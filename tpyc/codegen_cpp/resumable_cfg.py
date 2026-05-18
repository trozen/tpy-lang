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
- The CFG builder that walks an async def body.
- (Future task) Conservative cross-suspension liveness.
- (Future task) State assignment.

Emission is in `gen_async.py` (and, later, `gen_generators.py`); the
CFG itself is shape-neutral.

Generator migration plan: SuspensionPayload is a tagged union with two
variants today -- AwaitPayload (async, implemented) and YieldPayload
(generator, stub). The builder dispatches on which node it encounters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union

from ..parse.nodes import (
    TpyAssign, TpyAwait, TpyBreak, TpyContinue, TpyExceptHandler,
    TpyExpr, TpyExprStmt, TpyForEach, TpyIf, TpyName,
    TpyRaise, TpyReturn, TpyStmt, TpyTry, TpyVarDecl, TpyWhile, TpyWith,
    TpyWithItem,
)


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


@dataclass(frozen=True)
class YieldPayload:
    """A generator suspension. Stub for the future generator migration.

    Populated when the same CFG is built from a generator function body.
    """
    value_expr: TpyExpr | None    # the yielded expression (None for bare yield)


SuspensionPayload = Union[AwaitPayload, YieldPayload]


# -- Regions: try/finally/with frames active at a BB

@dataclass(frozen=True)
class TryRegion:
    """A try/except/finally frame active inside the try body.

    `handlers` are the source-level except handlers; emit wraps the
    case body in `catch (Type& e) { handler.body }` per handler. The
    finally body is in `finally_helper_name` (member-fn name); the
    helper is called on every throw / return / fall-through exit.
    """
    handlers: tuple[TpyExceptHandler, ...]
    finally_helper_name: str | None       # None if try has no finally body
    tier: str                              # "throw" / "return" / "finally_only"
    # Source-level loc for emit diagnostics.
    loc_source: TpyStmt | None = None


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
    a matched except)."""
    handler: TpyExceptHandler
    parent_finally: str | None = None
    loc_source: TpyStmt | None = None


@dataclass(frozen=True)
class WithRegion:
    """A with frame active inside the body. Emit wraps the case body in
    a try/catch that runs __exit__ with the exception args and either
    suppresses or re-raises depending on __exit__'s return.

    The with frame is currently "borrow" form -- the context manager
    object lives in a frame-hoisted local. exit_call_template is a
    function that, given the exception expr (or None) and indent,
    returns the C++ line(s) to invoke __exit__.
    """
    item: TpyWithItem
    cm_field_name: str            # frame field holding the cm object
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


Terminator = Union[Fall, Branch, Yield, ReturnT, RaiseT, Unreachable]


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
        cfg = builder.build_async(func_body)

    The builder maintains a "current BB" while walking statements.
    Compound statements that contain suspensions branch the CFG;
    statements that don't are appended to the current BB as leaf
    statements.
    """

    def __init__(self, payload_factory=None) -> None:
        """
        `payload_factory(await_node, host_stmt, kind, bind_target,
        return_stmt) -> AwaitPayload` is called for each top-level await
        the builder encounters. The factory fills in mode +
        sub_field_cpp_type, which are async-specific. If None, a
        placeholder payload is created (suitable for testing only).
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
        # (id(region), id(handler)) -> handler-entry BB id. Populated
        # by `_record_handler_entry` during try/except construction;
        # read by emitters via `get_handler_entry`.
        self._handler_entries: dict[tuple[int, int], int] = {}

    # -- Public entry point ---------------------------------------------

    def build_async(self, body: list[TpyStmt]) -> CFG:
        """Build a CFG from an async def's body statement list."""
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
            if _stmt_has_any_await(stmt) or force_loop_decomp:
                return self._build_if(cur, stmt)
        elif isinstance(stmt, TpyWhile):
            if _stmt_has_any_await(stmt):
                return self._build_while(cur, stmt)
        elif isinstance(stmt, TpyForEach):
            if _stmt_has_any_await(stmt):
                return self._build_for(cur, stmt)
        elif isinstance(stmt, TpyTry):
            if _stmt_has_any_await(stmt) or force_loop_decomp:
                return self._build_try(cur, stmt)
        elif isinstance(stmt, TpyWith):
            if _stmt_has_any_await(stmt) or force_loop_decomp:
                return self._build_with(cur, stmt)
        # Leaf statement (or compound without suspensions).
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
        exit_bb = self._new_bb()
        self._finish(cur, Fall(next_bb=check_bb))
        self._finish(check_bb, Branch(cond=stmt.condition, then_bb=body_bb,
                                       else_bb=exit_bb))
        self._loop_stack.append(_LoopCtx(
            continue_bb=check_bb,
            break_bb=exit_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=check_bb))
        finally:
            self._loop_stack.pop()
        # `else` clause runs after loop exits normally (no break). We
        # don't model break-vs-normal-exit distinction yet; reject if
        # orelse is present and contains a suspension. (Common case:
        # empty orelse, handled.)
        if stmt.orelse:
            orelse_end = self._build_block(exit_bb, stmt.orelse)
            if orelse_end is None:
                return None
            return orelse_end
        return exit_bb

    # -- for ------------------------------------------------------------

    def _build_for(self, cur: int, stmt: TpyForEach) -> int | None:
        # v1.5 M3.1 follow-up (see ASYNC_DESIGN.md): a sync `for x in
        # xs:` with an await in the body needs an iter/next desugaring.
        # Until that ships, reject at CFG-build time.
        raise _CFGNotYetSupported(
            "await inside a `for` body needs the for-loop desugaring "
            "pass (planned follow-up).",
            loc=stmt.loc,
        )

    # -- try/except/finally ---------------------------------------------

    def _build_try(self, cur: int, stmt: TpyTry) -> int | None:
        finally_name: str | None = None
        if stmt.finally_body:
            if _stmts_have_any_await(stmt.finally_body):
                raise _CFGNotYetSupported(
                    "await inside a `finally` body is a planned follow-up.",
                    loc=stmt.loc,
                )
            finally_name = f"__finally_{self._next_finally_id}"
            self._next_finally_id += 1
            self._finally_helpers.append((finally_name, list(stmt.finally_body)))
        try_region = TryRegion(
            handlers=tuple(stmt.handlers),
            finally_helper_name=finally_name,
            tier=stmt.tier or "throw",
            loc_source=stmt,
        )
        # join_bb is outside the try frame.
        join_bb = self._new_bb()

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
                        self._finish(else_end, Fall(next_bb=join_bb))
                else:
                    self._finish(try_end, Fall(next_bb=join_bb))
        finally:
            self._region_stack.pop()

        # Except-handler bodies: the corresponding TryRegion is no
        # longer active inside the handler (the throw has been caught);
        # the ExceptRegion takes its place so emit knows we're in a
        # handler body (for sub-future reset, exception-binding scope).
        for handler in stmt.handlers:
            except_region = ExceptRegion(
                handler=handler,
                parent_finally=try_region.finally_helper_name,
                loc_source=stmt,
            )
            self._region_stack.append(except_region)
            try:
                handler_entry = self._new_bb()
                handler_end = self._build_block(handler_entry, handler.body)
                if handler_end is not None:
                    self._finish(handler_end, Fall(next_bb=join_bb))
                self._record_handler_entry(try_region, handler, handler_entry)
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
        # Sync `with` containing an await is task 10. The baseline
        # rejects to preserve current behavior; lifted in that task.
        raise _CFGNotYetSupported(
            "await inside a `with` body is a planned follow-up.",
            loc=stmt.loc,
        )

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


def _stmt_has_any_await(stmt: TpyStmt) -> bool:
    """Walk a statement (its expression slots and sub_bodies) for any
    TpyAwait. Used for the lazy-decomposition decision."""

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
                if _stmt_has_any_await(s):
                    return True
    return False


def _stmts_have_any_await(stmts: list[TpyStmt]) -> bool:
    return any(_stmt_has_any_await(s) for s in stmts)


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


