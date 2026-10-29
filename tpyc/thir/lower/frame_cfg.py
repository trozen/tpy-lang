"""The resumable frame's coarse CFG builder: walks a resumable (async def /
generator) body and splits it into blocks at suspensions and their joins.

A compound statement that contains no suspension stays one leaf statement
of its block (lazy decomposition); one that does is decomposed into blocks
and terminators, with the try / except / finally / with / loop regions
active at each block recorded as its region stack.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from ...identity_map import IdentityMap, IdentitySet
from ...parse.nodes import (
    TpyAssert, TpyAssign, TpyAwait, TpyBreak, TpyContinue, TpyExceptHandler,
    TpyExpr, TpyExprStmt, TpyForEach, TpyIf, TpyName,
    TpyMatch, TpyNestedDef, TpyCall, TpyCoerce, TpyIntLiteral,
    TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral, TpyNoneLiteral,
    TpyRaise, TpyReturn, TpyStmt, TpyTry, TpyTupleUnpack, TpyVarDecl,
    TpyWhile, TpyWith,
    TpyYield, TryTier,
    stmt_has_any_suspension as _stmt_has_any_suspension,
    stmts_have_any_suspension as _stmts_have_any_suspension,
    stmts_have_any_return as _stmts_have_any_return,
)
from ..frame import (
    AsyncWithKind, AwaitKind, AwaitMode, THIRAsyncWithSetup,
    THIRAwaitSuspension, THIRBlock, THIRBranch, THIRExceptRegion, THIRFall,
    THIRFinallyExit, THIRFinallyRegion, THIRForAdvance, THIRForIterSetup,
    THIRFrameRaise, THIRFrameReturn, THIRLeafSlot, THIRLoopSourceRegion,
    THIRMatchDispatch, LeafRole, await_operand_slots,
    THIRRegion, THIRSuspend, THIRSuspensionPayload, THIRTerminator, THIRTryRegion, THIRFallOff,
    THIRWithEnter,
    THIRWithRegion, THIRYieldSuspension,
)

if TYPE_CHECKING:
    from ...typesys import TpyType


class FrameCFG(NamedTuple):
    """What `CFGBuilder.build` hands the frame builder, which publishes it
    on `THIRResumableFrame`."""
    entry_bb: int
    blocks: 'dict[int, THIRBlock]'
    yield_sites: 'list[THIRSuspend]'
    finally_helpers: 'list[tuple[str, tuple[THIRLeafSlot, ...]]]'
    decomposed: IdentitySet
    handler_entries: IdentityMap


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
    regions_at_entry: tuple[THIRRegion, ...]


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
                 for_uid_map: 'IdentityMap | None' = None,
                 with_uid_map: 'IdentityMap | None' = None,
                 try_finally_uid_map: 'IdentityMap | None' = None,
                 func_returns_void: bool = False,
                 loop_close_fields: 'IdentityMap | None' = None,
                 for_strategies: 'dict[int, str] | None' = None,
                 lent_step_vars: frozenset[str] = frozenset()) -> None:
        """
        `payload_factory(await_node, host_stmt, kind, bind_target,
        return_stmt) -> THIRAwaitSuspension` is called for each top-level await
        the builder encounters. The factory fills in mode +
        sub_field_cpp_type, which are async-specific. If None, a
        placeholder payload is created (suitable for testing only).

        `for_uid_map` maps a TpyForEach -> uid for for-loops that the
        caller has pre-scanned and registered with frame fields. Used by
        `_build_for` to attach the correct uid to THIRForIterSetup /
        THIRForAdvance. Missing entries cause `_build_for` to raise
        _CFGNotYetSupported (the caller is expected to pre-register every
        for-with-await loop).

        `with_uid_map` maps a TpyWith -> list of ctx_n (one per WithItem,
        in source order) for with-stmts pre-scanned and registered. Used
        by `_build_with`. Missing entries cause `_build_with` to raise.

        `loop_close_fields` maps a TpyForEach -> the frame fields a
        decomposed loop releases when control leaves it (a
        `THIRLoopSourceRegion`); a loop absent from it releases nothing early.

        `for_strategies` maps a decomposed loop's uid to its iteration
        strategy: a counted `range` loop renders its bounds rather than its
        iterable, so its setup holds a slot per bound. `lent_step_vars` are
        the loop vars bound from a lent step, whose advance renders it.
        """
        self._blocks: dict[int, THIRBlock] = {}
        self._next_bb_id: int = 0
        self._yield_sites: list[THIRSuspend] = []
        self._finally_helpers: list[tuple[str, tuple[THIRLeafSlot, ...]]] = []
        self._next_finally_id: int = 0
        # Construction-time stacks.
        self._loop_stack: list[_LoopCtx] = []
        self._region_stack: list[THIRRegion] = []
        # isinstance/`is not None` narrowing facts active at the current
        # construction point. New BBs are stamped with a snapshot so the
        # emitter can re-establish narrowed bindings at resume cases that
        # land inside a narrowed region (a binding is a C++ local that
        # does not survive the suspension that split the region).
        self._active_narrowings: dict[str, TpyType] = {}
        self._payload_factory = payload_factory
        self._for_uid_map: IdentityMap = (
            for_uid_map if for_uid_map is not None else IdentityMap())
        self._with_uid_map: IdentityMap = (
            with_uid_map if with_uid_map is not None else IdentityMap())
        self._try_finally_uid_map: IdentityMap = (
            try_finally_uid_map if try_finally_uid_map is not None
            else IdentityMap())
        self._func_returns_void: bool = func_returns_void
        self._loop_close_fields: IdentityMap = (
            loop_close_fields if loop_close_fields is not None
            else IdentityMap())
        # Try region -> handler -> handler-entry block id. Populated by
        # `_record_handler_entry` during try/except construction and
        # published on the frame (`THIRResumableFrame.handler_entry`).
        self._handler_entries: IdentityMap = IdentityMap()
        self._decomposed: IdentitySet = IdentitySet()
        self._for_strategies: dict[int, str] = dict(for_strategies or {})
        self._lent_step_vars = lent_step_vars

    # -- Public entry point ---------------------------------------------

    def build(self, body: list[TpyStmt]) -> FrameCFG:
        """Build a CFG from a resumable function body (async def or
        generator). Shape-neutral: a `yield` is a suspension point
        alongside `await`."""
        entry = self._new_bb()
        end = self._build_block(entry, body)
        # The tail block falls off the body's end. The emitter inserts the
        # appropriate "fell-through-without-returning" handling (Ready(unit)
        # for `-> None`, panic otherwise).
        if end is not None and self._blocks[end].terminator is None:
            self._blocks[end].terminator = THIRFallOff()
        cfg = FrameCFG(
            entry_bb=entry,
            blocks=self._blocks,
            yield_sites=self._yield_sites,
            finally_helpers=self._finally_helpers,
            decomposed=self._decomposed,
            handler_entries=self._handler_entries,
        )
        self._mark_resume_entries(cfg)
        return cfg

    # -- BB helpers -----------------------------------------------------

    def _new_bb(self) -> int:
        bb_id = self._next_bb_id
        self._next_bb_id += 1
        bb = THIRBlock(id=bb_id, region_stack=tuple(self._region_stack),
                entry_narrowings=dict(self._active_narrowings))
        self._blocks[bb_id] = bb
        return bb_id

    # -- Narrowing-fact threading ---------------------------------------

    def _push_narrowings(self, facts: 'dict[str, TpyType]') -> dict:
        """Extend the active narrowing set with `facts` (concrete-type
        narrowings only; union/None facts are no-ops at the binding
        level). Returns a token for `_pop_narrowings`."""
        prev = self._active_narrowings
        if facts:
            merged = dict(prev)
            merged.update(facts)
            self._active_narrowings = merged
        else:
            self._active_narrowings = dict(prev)
        return prev

    def _pop_narrowings(self, prev: dict) -> None:
        self._active_narrowings = prev

    def _kill_narrowings(self, stmt: TpyStmt) -> None:
        """Drop a variable's narrowing when a statement reassigns it, so
        BBs built after the reassignment don't re-cast a now-wrong type.
        Assert narrowings (`then_type_facts`) flow forward into the active
        set for the rest of the block."""
        if isinstance(stmt, TpyAssert):
            if stmt.then_type_facts:
                self._active_narrowings = {**self._active_narrowings,
                                           **stmt.then_type_facts}
            return
        killed: set[str] = set()
        if isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
            killed.add(stmt.target.name)
        elif isinstance(stmt, TpyVarDecl):
            killed.add(stmt.name)
        elif isinstance(stmt, TpyTupleUnpack):
            # `a, b = ...` rebinds each target to a fresh value; any narrowed
            # name among them must lose its narrowing (parser guarantees the
            # targets are simple names, `None` for `_`).
            killed.update(n for n in stmt.targets if n is not None)
        if killed & self._active_narrowings.keys():
            self._active_narrowings = {
                k: v for k, v in self._active_narrowings.items()
                if k not in killed}

    def _finish(self, bb_id: int, terminator: THIRTerminator) -> None:
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
            self._kill_narrowings(stmt)
        return cur

    def _build_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Process one statement. Returns the BB id where control flows
        out of this statement (None for unconditional terminators)."""
        # Statements with explicit terminator semantics.
        if isinstance(stmt, TpyReturn):
            return self._build_return(cur, stmt)
        if isinstance(stmt, TpyRaise):
            self._finish(cur, THIRFrameRaise(
                slot=THIRLeafSlot(LeafRole.RAISE, stmt)))
            return None
        if isinstance(stmt, TpyBreak):
            self._decomposed.add(stmt)
            self._build_break(cur)
            return None
        if isinstance(stmt, TpyContinue):
            self._decomposed.add(stmt)
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
                self._decomposed.add(stmt)
                return self._build_if(cur, stmt)
        elif isinstance(stmt, TpyWhile):
            if _stmt_has_any_suspension(stmt):
                self._decomposed.add(stmt)
                return self._build_while(cur, stmt)
        elif isinstance(stmt, TpyForEach):
            if stmt.is_async or _stmt_has_any_suspension(stmt):
                self._decomposed.add(stmt)
                return self._build_for(cur, stmt)
        elif isinstance(stmt, TpyTry):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                self._decomposed.add(stmt)
                return self._build_try(cur, stmt)
        elif isinstance(stmt, TpyWith):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                self._decomposed.add(stmt)
                return self._build_with(cur, stmt)
        elif isinstance(stmt, TpyMatch):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                self._decomposed.add(stmt)
                return self._build_match(cur, stmt)
        # Leaf statement (or compound without suspensions). If a statement
        # reaches this point STILL containing a suspension, it's a statement
        # kind the builder doesn't decompose (if/while/for/try/with/match all
        # are). Appending it as a leaf would emit the nested `await`/`yield`
        # as straight-line code -- silently wrong for async, and for a
        # generator it would collide with the resumable state enum. Refuse so
        # the build surfaces a clean CodeGenError instead of a miscompile.
        if _stmt_has_any_suspension(stmt):
            raise _CFGNotYetSupported(
                "a suspension (`await`/`yield`) inside this statement is "
                "not yet supported by the resumable lowering (the statement "
                "kind is not decomposed by the CFG builder).",
                loc=getattr(stmt, "loc", None),
            )
        self._blocks[cur].stmts.append(THIRLeafSlot(LeafRole.STMT, stmt))
        return cur

    # -- Top-level await on assign/vardecl/return/expr-stmt -------------

    def _stmt_has_top_level_await(self, stmt: TpyStmt) -> bool:
        return _top_level_await_in(stmt) is not None

    def _build_top_level_await_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Convert a statement whose top-level expression slot is a
        TpyAwait into a THIRSuspend terminator. The bind / discard / return
        action runs in the resume BB."""
        await_node = _top_level_await_in(stmt)
        assert await_node is not None
        kind, bind_target, return_stmt = _classify_await_position(stmt, await_node)
        if self._payload_factory is not None:
            payload = self._payload_factory(
                await_node, stmt, kind, bind_target, return_stmt)
        else:
            payload = THIRAwaitSuspension(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                host_stmt=stmt,
                await_node=await_node,
                **await_operand_slots(AwaitMode.INLINE, await_node.value),
            )
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = THIRSuspend(
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
            self._finish(resume_bb, THIRFrameReturn(
                slot=THIRLeafSlot(LeafRole.RETURN, return_stmt)))
            return None
        return resume_bb

    def _build_yield_stmt(self, cur: int, stmt: TpyYield) -> int | None:
        """Convert a `yield` statement into a THIRSuspend terminator. Unlike an
        `await`, a yield pushes its value OUT to the caller and resumes
        in place -- there is no sub-future to poll and (until `send()`)
        no value bound back in, so the resume BB simply continues after
        the yield. The generator emitter reads `value_expr` / `yield_stmt`
        off the payload to produce the yielded value."""
        payload = THIRYieldSuspension(
            value_expr=stmt.value, yield_stmt=stmt,
            value=THIRLeafSlot(LeafRole.YIELD_VALUE, stmt))
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = THIRSuspend(
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
        self._finish(cur, THIRFrameReturn(
            slot=THIRLeafSlot(LeafRole.RETURN, stmt)))
        return None

    # -- break / continue -----------------------------------------------

    def _build_break(self, cur: int) -> None:
        if not self._loop_stack:
            raise AssertionError("'break' outside a loop: sema rejects it")
        target = self._loop_stack[-1].break_bb
        self._finish(cur, THIRFall(next_bb=target))

    def _build_continue(self, cur: int) -> None:
        if not self._loop_stack:
            raise AssertionError("'continue' outside a loop: sema rejects it")
        target = self._loop_stack[-1].continue_bb
        self._finish(cur, THIRFall(next_bb=target))

    # -- if/else --------------------------------------------------------

    def _build_if(self, cur: int, stmt: TpyIf) -> int | None:
        then_bb = self._new_bb()
        else_bb = self._new_bb()
        join_bb = self._new_bb()
        self._finish(cur, THIRBranch(cond=THIRLeafSlot(LeafRole.COND,
                                                      stmt.condition),
                                     then_bb=then_bb,
                                  else_bb=else_bb))
        # Each arm carries the condition's narrowing facts so a suspension
        # inside the arm re-establishes the narrowed binding at its resume
        # case. then_bb/else_bb were created in the outer context, so stamp
        # them explicitly; continuation BBs are stamped by `_new_bb` while
        # the arm facts are pushed.
        self._blocks[then_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.then_type_facts}
        prev = self._push_narrowings(stmt.then_type_facts)
        then_end = self._build_block(then_bb, stmt.then_body)
        self._pop_narrowings(prev)
        if then_end is not None:
            self._finish(then_end, THIRFall(next_bb=join_bb))
        self._blocks[else_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.else_type_facts}
        prev = self._push_narrowings(stmt.else_type_facts)
        else_end = self._build_block(else_bb, stmt.else_body)
        self._pop_narrowings(prev)
        if else_end is not None:
            self._finish(else_end, THIRFall(next_bb=join_bb))
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
        self._finish(cur, THIRFall(next_bb=check_bb))
        self._finish(check_bb, THIRBranch(cond=THIRLeafSlot(LeafRole.COND,
                                                           stmt.condition),
                                          then_bb=body_bb,
                                       else_bb=exit_bb))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        self._loop_stack.append(_LoopCtx(
            continue_bb=check_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        # The condition's narrowing applies inside the loop body; a
        # suspension in the body re-establishes the binding at its resume.
        self._blocks[body_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.then_type_facts}
        prev = self._push_narrowings(stmt.then_type_facts)
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, THIRFall(next_bb=check_bb))
        finally:
            self._loop_stack.pop()
            self._pop_narrowings(prev)
        if stmt.orelse:
            # else runs on the normal-exit edge, then falls to after_bb;
            # break edges (-> after_bb) skip it. The else body is a regular
            # region so it may itself contain suspensions.
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, THIRFall(next_bb=after_bb))
            return after_bb
        return exit_bb

    # -- for ------------------------------------------------------------

    def _build_for(self, cur: int, stmt: TpyForEach) -> int | None:
        if stmt.is_async:
            return self._build_async_for(cur, stmt)
        # Universal iter/next lowering. The for-loop becomes:
        #   iter_init -> cond_advance --(has_value)-> body BBs --(fall)-> cond_advance
        #                            --(exhausted)-> exit
        # The cond/advance BB carries an THIRForAdvance terminator
        # which expands at emit to next() + has_value check + bind.
        uid = self._for_uid_map.get(stmt)
        if uid is None:
            raise _CFGNotYetSupported(
                "for-loop with await reached CFG builder without a "
                "registered uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        iter_init_bb = self._new_bb()
        # The advance and the body are the loop's; the setup is not, so that
        # entering the loop needs no state of its own. Exhaustion, `break`
        # and the `else` clause are outside it: CPython drops the iterator
        # before the `else` runs.
        close = self._loop_close_fields.get(stmt)
        # One object for every block of the loop: region stacks are compared
        # by identity.
        loop_region = (THIRLoopSourceRegion(uid=uid, fields=close)
                       if close else None)
        if loop_region is not None:
            self._region_stack.append(loop_region)
        try:
            cond_bb = self._new_bb()
            body_bb = self._new_bb()
        finally:
            if loop_region is not None:
                self._region_stack.pop()
        # `exit_bb` is the EXHAUSTED (normal-exit) target; the `else` clause
        # runs there. `break` targets `after_bb` (skips else). No else ->
        # after_bb IS exit_bb (non-else for-loops unchanged).
        exit_bb = self._new_bb()
        # THIRFall into iter init.
        self._finish(cur, THIRFall(next_bb=iter_init_bb))
        # iter_init_bb: setup, fall to cond. A counted loop renders its
        # bounds; every other strategy renders the source once (and the
        # `next` advance names it again).
        if self._for_strategies.get(uid, "iter_next") == "range":
            iterable = None
            bounds = tuple(THIRLeafSlot(LeafRole.RANGE_BOUND, a)
                           for a in stmt.iterable.args)
        else:
            iterable = THIRLeafSlot(LeafRole.FOR_ITERABLE, stmt.iterable)
            bounds = ()
        self._blocks[iter_init_bb].stmts.append(
            THIRForIterSetup(
                uid=uid, source=stmt.iterable, iterable=iterable,
                bounds=bounds, elem_type=stmt.elem_type,
                iter_borrow_unplaceable=stmt.iter_borrow_unplaceable))
        self._finish(iter_init_bb, THIRFall(next_bb=cond_bb))
        # cond_bb: THIRForAdvance terminator (advance + branch).
        self._finish(cond_bb, THIRForAdvance(
            uid=uid, stmt=stmt,
            has_value_bb=body_bb, exhausted_bb=exit_bb,
            iterable=iterable,
            step=(THIRLeafSlot(LeafRole.LOOP_STEP, stmt)
                  if stmt.var in self._lent_step_vars else None)))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        # body: continue=cond_bb, break=after_bb (skips the else).
        self._loop_stack.append(_LoopCtx(
            continue_bb=cond_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        if loop_region is not None:
            self._region_stack.append(loop_region)
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, THIRFall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()
            if loop_region is not None:
                self._region_stack.pop()
        if stmt.orelse:
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, THIRFall(next_bb=after_bb))
            return after_bb
        return exit_bb

    def _build_async_for(self, cur: int, stmt: TpyForEach) -> int | None:
        """Lower `async for y in ait: <body>` (v1.5 M6).

        Shape:
          iter_init_bb (THIRForIterSetup(is_async=True))
            -> THIRTryRegion[THIRExceptRegion(StopAsyncIteration -> break)] {
                 cond_bb: THIRSuspend(await __aiter.__anext__(), bind y)
                 resume_bb: (binds y, falls out of try)
               }
            (pop THIRTryRegion)
            body_bb: <user body>, THIRFall -> cond_bb
            handler -> exit_bb (via the loop's break_bb)

        The THIRTryRegion wraps ONLY the cond/resume pair, NOT the body.
        A StopAsyncIteration from the body must propagate up like any
        other exception, so the body's case bodies must not be wrapped
        in the auto-catch.

        Parser rejects `else:` on async-for, so no orelse handling here.
        """
        uid = self._for_uid_map.get(stmt)
        if uid is None:
            raise _CFGNotYetSupported(
                "async-for reached CFG builder without a registered "
                "uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        # body_bb / exit_bb are created OUTSIDE the THIRTryRegion push so a
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

        self._finish(cur, THIRFall(next_bb=iter_init_bb))
        self._blocks[iter_init_bb].stmts.append(
            THIRForIterSetup(
                uid=uid, source=stmt.iterable,
                iterable=THIRLeafSlot(LeafRole.ASYNC_FOR_ITERABLE,
                                      stmt.iterable),
                is_async=True,
                iter_borrow_unplaceable=stmt.iter_borrow_unplaceable))

        # The handler body is a synthesized TpyBreak -- _build_block
        # routes it through _loop_stack to the loop's break_bb, so
        # _loop_stack must be active while the handler is built.
        handler = TpyExceptHandler(
            exception_type="StopAsyncIteration",
            binding=None,
            body=[TpyBreak(loc=stmt.loc)],
            loc=stmt.loc,
        )
        try_region = THIRTryRegion(
            handlers=(handler,),
            finally_helper_name=None,
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
            self._finish(iter_init_bb, THIRFall(next_bb=cond_bb))
            dummy_await = TpyAwait(value=stmt.iterable, loc=stmt.loc)
            payload = THIRAwaitSuspension(
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
            yield_term = THIRSuspend(
                payload=payload,
                resume_bb=resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(cond_bb, yield_term)
            self._yield_sites.append(yield_term)
            # Build the synthesized except handler body (single TpyBreak)
            # while the THIRTryRegion is still on the stack so the
            # THIRExceptRegion has the right parent.
            except_region = THIRExceptRegion(
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

        # body_bb sits outside the THIRTryRegion -- a user `await` in the
        # body whose result throws StopAsyncIteration propagates up
        # rather than being silently swallowed.
        self._finish(resume_bb, THIRFall(next_bb=body_bb))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, THIRFall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()

        return exit_bb

    # -- try/except/finally ---------------------------------------------

    def _build_try(self, cur: int, stmt: TpyTry) -> int | None:
        if stmt.tier is TryTier.RETURN and stmt.handled_error_return:
            # Sema classified the handlers as ReturnException ones, so the
            # only thing that can enter them is a failing @error_return
            # call -- never a C++ throw. A decomposed try has no way to
            # spell that edge yet (the handler is another basic block, and
            # the unwrap check has no state transition to take), so the
            # region would emit a catch nothing reaches and the calls in
            # its body would take the unhandled disposition. Reject instead
            # of emitting that. A False flag means no EXPLICIT call: those
            # are admitted at one sema chokepoint
            # (`_check_error_return_handled`), and the implicit caller that
            # would otherwise bypass it -- a `with` header's
            # `__enter__`/`__exit__` -- is rejected in `_analyze_with`. The
            # other implicit callers (an `@error_return` `__getitem__` or
            # `__add__`) never reach this function at all: the record's
            # generated operator bridge returns the raw `std::expected` and
            # the C++ build fails at the class definition
            # (BUGS.md#error-return-operator-bridge-unlocated). So no
            # reachable shape leaves the flag False with a failing call in
            # the body: the handler is unreachable, the dead catch the
            # region emits is harmless, and the shape keeps compiling.
            raise _CFGNotYetSupported(
                "'except StopIteration' (or any other ReturnException type) "
                "is not yet supported inside a generator or 'async def' when "
                "the 'try' needs its own states -- it holds an "
                "'await'/'yield', or a 'break'/'continue' that leaves it. "
                "Move the 'try' into a helper function and call that, or "
                "keep the 'try' free of suspensions and of transfers out of "
                "it.",
                loc=stmt.loc,
            )
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
            self._finally_helpers.append((finally_name, tuple(
                THIRLeafSlot(LeafRole.FINALLY_STMT, s)
                for s in stmt.finally_body)))
        elif finally_async:
            # CFG-based finally. Pre-scan assigns an exception-slot uid
            # and (if returns are reachable in any of try / handler /
            # finally) a pending-return slot. Nesting two CFG-based
            # finally regions is supported: the inner THIRFinallyExit
            # forwards its pending state into the outer's parking slot
            # (see `_emit_async_finally_exit`).
            uid = self._try_finally_uid_map.get(stmt)
            if uid is None:
                raise _CFGNotYetSupported(
                    "try-finally-with-await reached CFG builder "
                    "without a registered exception-slot uid "
                    "(internal: pre-scan missed it).",
                    loc=stmt.loc,
                )
            captured_exc_field = f"__finally_exc_{uid}"
            # Pending-return slot: allocated when there's a reachable
            # `return` anywhere the CFG-based finally can intercept --
            # try body, any handler body, or the finally body itself
            # (the THIRFinallyRegion mirrors the flag/slot so a return inside
            # the finally body parks into the same place).
            # `pending_return_slot` is None for void async defs (the
            # flag alone suffices).
            try_has_return = (_stmts_have_any_return(stmt.try_body)
                               or any(_stmts_have_any_return(h.body)
                                       for h in stmt.handlers)
                               or _stmts_have_any_return(stmt.finally_body))
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

        # Pre-allocate finally_entry_bb + finally_exit_bb (CFG-based
        # finally only) so THIRTryRegion / THIRFinallyRegion can carry them.
        # `finally_exit_bb` is the single replay site holding the
        # THIRFinallyExit synth -- both normal fall-through from the
        # finally body and a return-in-finally jump here, which gives
        # the deferred-return replay a home outside the body's last BB.
        finally_region: THIRFinallyRegion | None = None
        finally_exit_bb: int | None = None
        if finally_async:
            assert captured_exc_field is not None
            # finally_exit_bb is OUTSIDE the THIRFinallyRegion -- by the time
            # THIRFinallyExit runs, the finally body is done.
            finally_exit_bb = self._new_bb()
            finally_region = THIRFinallyRegion(
                helper_name=captured_exc_field,
                loc_source=stmt,
                pending_return_flag=pending_return_flag,
                pending_return_slot=pending_return_slot,
                finally_exit_bb=finally_exit_bb,
            )
            self._region_stack.append(finally_region)
            finally_entry_bb = self._new_bb()
            self._region_stack.pop()

        try_region = THIRTryRegion(
            handlers=tuple(stmt.handlers),
            finally_helper_name=finally_name,
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

        # Try body BBs live inside the THIRTryRegion. Push the region BEFORE
        # creating them so their region_stack captures correctly.
        self._region_stack.append(try_region)
        try:
            try_body_bb = self._new_bb()
            self._finish(cur, THIRFall(next_bb=try_body_bb))
            try_end = self._build_block(try_body_bb, stmt.try_body)
            if try_end is not None:
                if stmt.else_body:
                    else_end = self._build_block(try_end, stmt.else_body)
                    if else_end is not None:
                        self._finish(else_end,
                                      THIRFall(next_bb=normal_exit_target))
                else:
                    self._finish(try_end,
                                  THIRFall(next_bb=normal_exit_target))
        finally:
            self._region_stack.pop()

        # Except-handler bodies: the corresponding THIRTryRegion is no
        # longer active inside the handler (the throw has been caught);
        # the THIRExceptRegion takes its place so emit knows we're in a
        # handler body (for sub-future reset, exception-binding scope).
        # When the parent try has a CFG-based finally, the
        # THIRExceptRegion also carries the parent's captured_exc /
        # finally_entry so emit can route raises and returns inside
        # the handler body through the finally region.
        for handler in stmt.handlers:
            except_region = THIRExceptRegion(
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
                                  THIRFall(next_bb=normal_exit_target))
                self._record_handler_entry(try_region, handler, handler_entry)
            finally:
                self._region_stack.pop()

        # Finally body (CFG-based path).
        if finally_async:
            assert finally_region is not None
            assert finally_entry_bb is not None
            assert finally_exit_bb is not None
            assert captured_exc_field is not None
            self._region_stack.append(finally_region)
            try:
                finally_end = self._build_block(
                    finally_entry_bb, stmt.finally_body)
                if finally_end is not None:
                    # Normal fall-through: route through finally_exit_bb
                    # so THIRFinallyExit (rethrow + deferred-return
                    # replay) runs at the single replay site. A
                    # return-in-finally inside the body also jumps to
                    # finally_exit_bb (via the THIRFinallyRegion's parking
                    # info -- see gen_async._pending_return_info_for_region_stack).
                    self._finish(finally_end, THIRFall(next_bb=finally_exit_bb))
            finally:
                self._region_stack.pop()
            # THIRFinallyExit lives at the dedicated exit BB; fall to
            # join from there.
            self._blocks[finally_exit_bb].stmts.append(
                THIRFinallyExit(
                    captured_exc_field=captured_exc_field,
                    pending_return_flag=pending_return_flag,
                    pending_return_slot=pending_return_slot,
                ))
            self._finish(finally_exit_bb, THIRFall(next_bb=join_bb))

        return join_bb

    def _record_handler_entry(self, region: THIRTryRegion,
                               handler: TpyExceptHandler,
                               entry_bb: int) -> None:
        # A side map rather than a region field: the region is frozen and
        # built before its handlers' blocks exist.
        self._handler_entries.setdefault(region, IdentityMap())[handler] = entry_bb
        self._decomposed.add(handler)

    # -- with -----------------------------------------------------------

    def _build_with(self, cur: int, stmt: TpyWith) -> int | None:
        """Lower a sync `with X as t: body` whose body contains await
        into a CFG region. The context manager lives in a frame-hoisted
        field (`__with_ctx_<n>`); the case-emit wraps each case body in
        try/catch that runs `__exit__` on exception (with optional
        suppression -> transition to post-with state).

        Async `with` (`stmt.is_async`) routes to a separate path: the
        `__aenter__` and `__aexit__` calls are themselves suspensions
        and need THIRSuspend BBs, modelled via a synthetic THIRTryRegion whose
        CFG-based finally body holds the `__aexit__` yield. See
        `_build_async_with`."""
        if stmt.is_async:
            return self._build_async_with(cur, stmt)
        ctx_ns = self._with_uid_map.get(stmt)
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
        # For each item, push THIRWithRegion then emit THIRWithEnter into
        # the current BB. Items are processed in source order
        # (outer-most CM enters first).
        regions: list[THIRWithRegion] = []
        for item, ctx_n in zip(stmt.items, ctx_ns):
            region = THIRWithRegion(
                item=item,
                ctx_n=ctx_n,
                post_with_bb=post_with_bb,
                loc_source=stmt,
            )
            self._blocks[cur].stmts.append(
                THIRWithEnter(ctx_n=ctx_n, item=item,
                              manager=THIRLeafSlot(LeafRole.WITH_MANAGER,
                                                   item.context_expr)))
            self._region_stack.append(region)
            regions.append(region)
        try:
            # Body BBs live inside all WithRegions.
            body_entry = self._new_bb()
            self._finish(cur, THIRFall(next_bb=body_entry))
            body_end = self._build_block(body_entry, stmt.body)
            if body_end is not None:
                # THIRFall out -> exit BB. THIRRegion exit (normal __exit__) is
                # emitted via `_emit_exit_region_finallies` when the
                # transition crosses out of the With regions.
                self._finish(body_end, THIRFall(next_bb=post_with_bb))
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
          THIRAsyncWithSetup -> THIRSuspend(__aenter__) -> [THIRTryRegion {
              <recurse to next item, or body>
          } finally {
              THIRSuspend(__aexit__) ; THIRFinallyExit
          }]

        The THIRTryRegion's finally body re-uses the M3.3 CFG-based-finally
        machinery: a `__finally_exc_<uid>` frame slot saves any in-flight
        exception via `std::current_exception()`; THIRFinallyExit at
        the tail rethrows. `return` inside the body parks the value in
        `__finally_pending_<uid>` / `__finally_ret_<uid>` and walks
        through the same finally path (same as M3.3.2).

        v1.5 M5 cleanup-only: `__aexit__` is called with all-None args
        regardless of whether an exception was caught. Inspecting
        `exc_val: Optional[BaseException]` is deferred to the
        polymorphic-exception-storage milestone (E9).
        """
        ctx_ns = self._with_uid_map.get(stmt)
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
        finally_uid = self._try_finally_uid_map.get(stmt)
        if finally_uid is None:
            raise _CFGNotYetSupported(
                "async-with reached CFG builder without registered "
                "finally-exc uid (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        # Multi-item `async with X as a, Y as b:`: would left-to-right
        # desugar to nested `async with` using the same finally_uid, so
        # the items would collide on the shared `__finally_exc_<n>` /
        # `__finally_pending_<n>` / `__finally_ret_<n>` fields. Reject
        # for now; users can nest two `async with` statements instead.
        if len(stmt.items) != 1:
            raise _CFGNotYetSupported(
                "multi-item `async with X as a, Y as b:` is not yet "
                "supported -- nest two `async with` statements instead",
                loc=stmt.loc,
            )
        # An `async with` synthesizes its own THIRTryRegion with a CFG-based
        # finally body (the `__aexit__` THIRSuspend). Plain try/finally
        # nesting was lifted by extending THIRFinallyExit to forward
        # parked state outward; the async-with case has not been lifted
        # because the synthesized finally also needs to keep its
        # __aexit__ call ordering correct across forwarding.
        for r in self._region_stack:
            if (isinstance(r, THIRTryRegion)
                    and r.captured_exc_field is not None):
                raise _CFGNotYetSupported(
                    "`async with` nested inside another `await`-in-"
                    "finally region is a planned follow-up.",
                    loc=stmt.loc,
                )

        item = stmt.items[0]
        ctx_n = ctx_ns[0]
        captured_exc_field = f"__finally_exc_{finally_uid}"

        # 1. THIRAsyncWithSetup populates the __with_ctx_<n> frame slot.
        self._blocks[cur].stmts.append(
            THIRAsyncWithSetup(ctx_n=ctx_n, item=item,
                               manager=THIRLeafSlot(LeafRole.WITH_MANAGER,
                                                    item.context_expr)))

        # 2. THIRSuspend for `__aenter__()`. Synthesize a minimal TpyAwait so
        # the payload's dataclass invariants hold; emit branches on
        # `async_with_kind` and never reads the AST. The sub-coro
        # struct's C++ name is resolved at codegen time by looking up
        # `_async_with_struct_names[ctx_n]` (populated by the prescan).
        dummy_await = TpyAwait(value=item.context_expr, loc=stmt.loc)
        aenter_resume_bb = self._new_bb()
        aenter_payload = THIRAwaitSuspension(
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
        aenter_yield = THIRSuspend(
            payload=aenter_payload,
            resume_bb=aenter_resume_bb,
            suspension_index=len(self._yield_sites),
        )
        self._finish(cur, aenter_yield)
        self._yield_sites.append(aenter_yield)

        # 3. Build THIRTryRegion with CFG-based finally. The body is
        # stmt.body; the finally body holds the __aexit__ THIRSuspend +
        # THIRFinallyExit.
        pending_return_flag = None
        pending_return_slot = None
        if _stmts_have_any_return(stmt.body):
            pending_return_flag = f"__finally_pending_{finally_uid}"
            if not self._func_returns_void:
                pending_return_slot = f"__finally_ret_{finally_uid}"

        # Build finally body BB (with THIRFinallyRegion on stack).
        finally_region = THIRFinallyRegion(
            helper_name=captured_exc_field,
            loc_source=stmt,
        )
        self._region_stack.append(finally_region)
        finally_entry_bb = self._new_bb()
        self._region_stack.pop()

        try_region = THIRTryRegion(
            handlers=(),
            finally_helper_name=None,
            loc_source=stmt,
            finally_entry_bb=finally_entry_bb,
            captured_exc_field=captured_exc_field,
            pending_return_flag=pending_return_flag,
            pending_return_slot=pending_return_slot,
        )
        join_bb = self._new_bb()

        # Try body lives inside THIRTryRegion.
        self._region_stack.append(try_region)
        try:
            try_body_entry = self._new_bb()
            self._finish(aenter_resume_bb, THIRFall(next_bb=try_body_entry))
            body_end = self._build_block(try_body_entry, stmt.body)
            if body_end is not None:
                self._finish(body_end, THIRFall(next_bb=finally_entry_bb))
        finally:
            self._region_stack.pop()

        # Finally body: THIRSuspend for __aexit__ + THIRFinallyExit + THIRFall
        # to join.
        self._region_stack.append(finally_region)
        try:
            dummy_aexit_await = TpyAwait(
                value=item.context_expr, loc=stmt.loc)
            aexit_resume_bb = self._new_bb()
            aexit_payload = THIRAwaitSuspension(
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
            aexit_yield = THIRSuspend(
                payload=aexit_payload,
                resume_bb=aexit_resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(finally_entry_bb, aexit_yield)
            self._yield_sites.append(aexit_yield)
            # After aexit resume: THIRFinallyExit (rethrow saved exc if
            # any, deferred return if pending), then THIRFall to join.
            self._blocks[aexit_resume_bb].stmts.append(
                THIRFinallyExit(
                    captured_exc_field=captured_exc_field,
                    pending_return_flag=pending_return_flag,
                    pending_return_slot=pending_return_slot,
                ))
            self._finish(aexit_resume_bb, THIRFall(next_bb=join_bb))
        finally:
            self._region_stack.pop()

        return join_bb

    # -- match -----------------------------------------------------------

    def _build_match(self, cur: int, stmt: TpyMatch) -> int | None:
        """Lower a `match` whose arm bodies contain a suspension. The
        dispatch stays a single suspension-free unit (emitted later by
        reusing the ordinary match dispatch); each arm body becomes its
        own BB chain (recursively built, so nested suspensions decompose),
        joining at `join_bb`. `match` introduces no region, so arm BBs and join
        share `cur`'s region stack -- no finally-chain delta on the
        dispatch->arm or arm->join transitions.

        A guard or the subject expression cannot host a suspension (those
        appear only at statement position), so the dispatch is always
        suspension-free; only arm bodies suspend."""
        join_bb = self._new_bb()
        arm_bbs: list[int] = []
        all_terminate = True
        for case in stmt.cases:
            arm_bb = self._new_bb()
            arm_bbs.append(arm_bb)
            # The arm's pattern/guard narrowing (case.type_facts) applies to
            # the body; stamp the arm BBs so a suspension inside the arm
            # re-establishes the subject narrowing at the resume case (parallel
            # to _build_if). The pattern test + guard run in the suspension-free
            # dispatch, so only case.body -- which executes after they succeed
            # -- is stamped.
            self._blocks[arm_bb].entry_narrowings = {
                **self._active_narrowings, **case.type_facts}
            prev = self._push_narrowings(case.type_facts)
            arm_end = self._build_block(arm_bb, case.body)
            self._pop_narrowings(prev)
            if arm_end is not None:
                self._finish(arm_end, THIRFall(next_bb=join_bb))
                all_terminate = False
        # When the match is exhaustive AND every arm terminates, no
        # control path reaches past the match: the dispatch has no
        # fall-through and join is unreachable.
        reachable_join = None if (stmt.is_exhaustive and all_terminate) else join_bb
        self._finish(cur, THIRMatchDispatch(
            slot=THIRLeafSlot(LeafRole.MATCH_DISPATCH, stmt),
            arm_bbs=tuple(arm_bbs),
            join_bb=reachable_join,
        ))
        return reachable_join

    # -- post-construction passes ---------------------------------------

    def _mark_resume_entries(self, cfg: FrameCFG) -> None:
        for y in cfg.yield_sites:
            cfg.blocks[y.resume_bb].is_resume_entry = True


def case_entry_order(cfg: FrameCFG) -> tuple[int, ...]:
    """Every block that needs its own case label in the emitted switch,
    in discovery order (the printer numbers the labels in this order).

    Rules: a BB is a case entry iff it is
    - the entry BB (S_INITIAL),
    - the resume_bb of a Yield (S_RESUME_<i>),
    - reached via Fall/Branch by 2+ predecessors (multi-pred join), or
    - reached via Fall/Branch from a predecessor with a different
      region_stack (region-crossing edge).

    Handler entries (reached only via C++ catch) are inlined into
    their enclosing try-region's catch and never get a case label.
    """
    case_entries: dict[int, None] = {cfg.entry_bb: None}
    for y in cfg.yield_sites:
        case_entries[y.resume_bb] = None
    # Compute predecessors via Fall/Branch/Yield-resume edges.
    # Yield.resume_bb already a case entry; tracking helps detect
    # multi-pred / region-crossing.
    preds: dict[int, list[int]] = {bid: [] for bid in cfg.blocks}
    for bid, bb in cfg.blocks.items():
        t = bb.terminator
        if isinstance(t, THIRFall):
            preds[t.next_bb].append(bid)
        elif isinstance(t, THIRBranch):
            preds[t.then_bb].append(bid)
            preds[t.else_bb].append(bid)
        elif isinstance(t, THIRForAdvance):
            preds[t.has_value_bb].append(bid)
            preds[t.exhausted_bb].append(bid)
        elif isinstance(t, THIRSuspend):
            preds[t.resume_bb].append(bid)
    # Multi-pred (excluding yield-resume, which is already case entry).
    for bid, ps in preds.items():
        if bid in case_entries:
            continue
        if len(ps) >= 2:
            case_entries[bid] = None
    # Region-crossing predecessors.
    for bid, bb in cfg.blocks.items():
        if bid in case_entries:
            continue
        for p in preds.get(bid, ()):
            p_bb = cfg.blocks[p]
            if p_bb.region_stack != bb.region_stack:
                case_entries[bid] = None
                break
    # WithRegion suppression targets: any with whose __exit__ may
    # suppress an exception transitions state directly to
    # post_with_bb from inside the catch (not a normal CFG edge).
    # Mark every such post_with_bb as a case entry so the catch's
    # `__state = ...; continue;` lands on a labeled case.
    with_post_bbs: set[int] = set()
    for bb in cfg.blocks.values():
        for r in bb.region_stack:
            if (isinstance(r, THIRWithRegion)
                    and r.item.exit_can_suppress):
                with_post_bbs.add(r.post_with_bb)
    for bid in with_post_bbs:
        if bid not in case_entries:
            case_entries[bid] = None
    # CFG-based finally entry BBs: the TryRegion's catch-all
    # transitions state directly to `finally_entry_bb` from inside
    # the catch (not a normal CFG edge). The Fall on the normal
    # exit path region-crosses out of the TryRegion which already
    # makes this BB a case entry, but mark explicitly to cover the
    # "try body always raises" case (no Fall predecessor).
    # Same for CFG-based finally EXIT BBs: a `return` inside the
    # finally body transitions state directly here without a CFG
    # edge, so the BB must have a case label even if the
    # Fall-from-finally-end region-crossing already picked it up.
    finally_entry_bbs: set[int] = set()
    finally_exit_bbs: set[int] = set()
    for bb in cfg.blocks.values():
        for r in bb.region_stack:
            if (isinstance(r, THIRTryRegion)
                    and r.finally_entry_bb is not None):
                finally_entry_bbs.add(r.finally_entry_bb)
            elif (isinstance(r, THIRFinallyRegion)
                    and r.finally_exit_bb is not None):
                finally_exit_bbs.add(r.finally_exit_bb)
    for bid in finally_entry_bbs | finally_exit_bbs:
        if bid not in case_entries:
            case_entries[bid] = None
    # MatchDispatch join BBs: the dispatch transitions to its join
    # directly (a non-CFG edge from the suspension-free dispatch's
    # fall-through), and each arm body Falls there. The arm bodies
    # are INLINED by the dispatch (not their own cases), so arm_bbs
    # are deliberately NOT marked here; only the shared join needs a
    # label.
    match_join_bbs: set[int] = set()
    for bb in cfg.blocks.values():
        t = bb.terminator
        if isinstance(t, THIRMatchDispatch) and t.join_bb is not None:
            match_join_bbs.add(t.join_bb)
    for bid in match_join_bbs:
        if bid not in case_entries:
            case_entries[bid] = None
    return tuple(case_entries)

def collect_frame_nested_defs(stmts: 'list[TpyStmt]') -> 'list[TpyNestedDef]':
    """Nested defs anywhere in a resumable body, in source order. Each is
    emitted as a member function of the frame struct (locals are frame
    fields a lambda cannot capture, and a member is callable from every
    resume case). Nested-in-nested defs are rejected by sema."""
    out: 'list[TpyNestedDef]' = []
    for s in stmts:
        if isinstance(s, TpyNestedDef):
            out.append(s)
        else:
            for body in s.sub_bodies():
                out.extend(collect_frame_nested_defs(body))
    return out


def no_throw_blocks(cfg: FrameCFG) -> frozenset[int]:
    """The blocks whose case body provably cannot throw anything an
    in-scope handler would catch (`_case_is_no_throw`): the printer
    emits their case without the region try/catch."""
    return frozenset(bid for bid in cfg.blocks if _case_is_no_throw(cfg, bid))


def _case_is_no_throw(cfg: FrameCFG, entry_bb: int) -> bool:
    """True iff the case body that starts at `entry_bb` provably
    cannot throw anything an in-scope handler would catch.
    Conservative: requires the BB to consist solely of a Yield
    terminator with literal / simple-name emplace args, with no
    user stmts and not a resume entry (resume cases emit a cancel
    check + sub poll that can both throw)."""
    bb = cfg.blocks[entry_bb]
    if bb.stmts:
        return False
    if any(y.resume_bb == entry_bb for y in cfg.yield_sites):
        return False
    t = bb.terminator
    if not isinstance(t, THIRSuspend):
        return False
    return _payload_args_no_throw(t.payload)


def _payload_args_no_throw(payload: 'THIRSuspensionPayload') -> bool:
    """True if emitting the Yield cannot throw. For a generator yield,
    the emitted action is `return <value>;` -- no-throw iff the value
    is a literal / simple name."""
    if isinstance(payload, THIRYieldSuspension):
        return (payload.value_expr is None
                or _expr_is_simple(payload.value_expr))
    # Async-with synthetic yields emplace from the CM frame slot
    # (`*__with_ctx_<n>`) plus monostate literals -- provably
    # no-throw and never touch the payload's `operand_expr`.
    if payload.async_with_kind is not None:
        return True
    if payload.mode is AwaitMode.BORROWED:
        return _expr_is_simple(payload.operand_expr)
    if payload.mode is AwaitMode.ERASED:
        return _expr_is_simple(payload.operand_expr)
    # INLINE: emplace args are the call's args.
    call = payload.operand_expr
    if not isinstance(call, TpyCall):
        return False
    return all(_expr_is_simple(a) for a in call.args)


def _expr_is_simple(expr: TpyExpr) -> bool:
    """True for literals, bare names, and shallow coercions of
    either. These can be emitted without invoking constructors /
    function calls that could throw."""
    if isinstance(expr, TpyCoerce):
        return _expr_is_simple(expr.expr)
    return isinstance(expr, (TpyIntLiteral, TpyFloatLiteral,
                               TpyStrLiteral, TpyBoolLiteral,
                               TpyNoneLiteral, TpyName))


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


# -- Helpers -------------------------------------------------------------

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



