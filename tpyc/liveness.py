"""Last-use liveness analysis for auto-move optimization.

Pure AST walking -- no type registry or analysis context needed.
Run once per function/method in sema; results consumed by both sema and codegen.

The analysis identifies TpyName nodes that are the "last use" of a variable --
meaning the variable is not read again on any subsequent execution path (without
being reassigned first). These sites are candidates for std::move() in codegen.

Alias awareness: when alias = h creates a T& reference, auto-move of h is
suppressed if alias is still live (prevents dangling reference through alias).
When the source is reassigned (h = new_value), the alias points to old storage
and no longer constrains moves of the new h (detach-on-reassign).
"""

from __future__ import annotations

from .identity_map import IdentitySet
from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyReturn, TpyBreak, TpyRaise,
    TpyMatch, TpyNestedDef, TpyDelVar, TpyTry, TpyWith, TpyNonlocal,
    TpyName, TpyFieldAccess, TpySubscript, TpyNamedExpr, TpyFunction,
    TpyBoolLiteral, TpyAssert, TpyTupleLiteral,
)
from .parse.nodes import stmts_have_any_suspension, written_names

# source_name -> set[alias_name] reverse map
_Aliases = dict[str, set[str]]


def analyze_last_uses(
    stmts: list[TpyStmt],
    alias_sources: dict[str, str] | None = None,
) -> IdentitySet:
    """Analyze a function body to find last-use sites for auto-move.

    Returns the TpyName nodes that are at their last use --
    the variable is not read again on any subsequent execution path without
    being reassigned first.

    When alias_sources is provided (alias_name -> source_name), auto-move of
    a source variable is suppressed if any of its T& aliases are still live.

    The analysis is conservative: if unsure, a node is NOT marked as last use.
    A missed optimization is just a copy (same as current behavior).

    Also stamps `TpyWithItem.target_read_after` on the way through (see
    _analyze_with) -- the same live sets answer it, and a body this never
    walks keeps the field's conservative default.
    """
    source_aliases = _build_source_aliases(alias_sources) if alias_sources else {}
    # Per-alias detachment: aliases created before their source's reassignment
    # point to old storage and don't constrain moves of the new value.
    # Re-activated at the reassignment point going backward.
    detached_aliases, first_reassign_pos = (
        _compute_alias_detachment(stmts, source_aliases, alias_sources)
        if alias_sources else (set(), {})
    )
    # A nested def captures by reference and stays callable until function
    # end, so its captured names are live on every path after (and at) the
    # def. Seeding them here protects reads AFTER the def site -- the
    # backward walk only re-adds them when it reaches the def statement,
    # which protects reads before it. A reassignment between the def and a
    # later consume still kills the seed, which is sound: the closure reads
    # the rebound variable, not the old object.
    live: set[str] = _collect_nested_def_captures(stmts)
    last_uses: IdentitySet = IdentitySet()
    _analyze_stmts_backward(
        stmts, live, last_uses, source_aliases,
        detached_aliases, first_reassign_pos,
    )
    return last_uses


def _nested_def_captures(stmt: TpyNestedDef) -> set[str]:
    """What a nested def reads from the enclosing scope.

    Always the syntactic approximation: this pass runs as a prescan, so sema
    has not filled `captured_names` yet. Keying on that field being empty
    would silently swap mechanisms the day capture analysis moves earlier, so
    the choice is spelled out instead.
    """
    return _free_names_approx(stmt.func)


def _collect_nested_def_captures(stmts: list[TpyStmt]) -> set[str]:
    """Names captured by any nested def anywhere in the body (recursive).

    Liveness runs before sema's capture analysis populates
    TpyNestedDef.captured_names, so this uses a syntactic free-name
    over-approximation instead. Surplus names (globals, builtins) are
    harmless: they are never movable locals of the enclosing function.
    """
    captured: set[str] = set()
    for stmt in stmts:
        if isinstance(stmt, TpyNestedDef):
            captured |= _free_names_approx(stmt.func)
        for body in stmt.sub_bodies():
            captured |= _collect_nested_def_captures(body)
    return captured


def _free_names_approx(func: TpyFunction) -> set[str]:
    """Names a nested def reads from its enclosing scope: body reads minus
    its params and minus names it assigns (an assigned name is local in
    Python unless declared nonlocal, and reading a local before assignment
    is an UnboundLocalError, so read+assigned implies local).
    """
    params = {name for name, _ in func.params}
    reads: set[str] = set()
    assigned: set[str] = set()
    forced: set[str] = set()

    def walk(body: list[TpyStmt]) -> None:
        for s in body:
            if isinstance(s, TpyVarDecl):
                assigned.add(s.name)
            elif isinstance(s, TpyTupleUnpack):
                assigned.update(n for n in s.targets if n is not None)
            elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
                assigned.add(s.target.name)
            elif isinstance(s, TpyForEach):
                assigned.add(s.var)
            elif isinstance(s, TpyWith):
                assigned.update(
                    i.target for i in s.items if i.target is not None)
            elif isinstance(s, TpyTry):
                assigned.update(
                    h.binding for h in s.handlers if h.binding is not None)
            elif isinstance(s, TpyNonlocal):
                forced.update(s.names)
            elif isinstance(s, TpyNestedDef):
                forced.update(_free_names_approx(s.func))
            walrus: set[str] = set()
            for e in s.exprs():
                reads.update(n.name for n in _collect_reads_expr(e, walrus))
            assigned.update(walrus)
            for b in s.sub_bodies():
                walk(b)

    walk(func.body)
    return (reads - assigned - params) | forced


# -- Alias map helpers --------------------------------------------------------

def _build_source_aliases(alias_sources: dict[str, str]) -> _Aliases:
    """Build source -> set[alias] reverse map with transitive resolution.

    Given alias_sources {alias -> source}, resolves chains like
    a -> h, b -> a to a -> h, b -> h, then builds reverse map h -> {a, b}.
    """
    resolved: dict[str, str] = {}
    for alias in alias_sources:
        source = alias
        visited: set[str] = set()
        while source in alias_sources and source not in visited:
            visited.add(source)
            source = alias_sources[source]
        resolved[alias] = source
    reverse: _Aliases = {}
    for alias, source in resolved.items():
        reverse.setdefault(source, set()).add(alias)
    return reverse


def _has_live_alias(
    name: str, live: set[str], source_aliases: _Aliases,
    detached_aliases: set[str],
) -> bool:
    """Check if any active T& alias of 'name' is in the live set.

    Aliases in detached_aliases were created before their source was reassigned,
    so they point to old storage and don't constrain moves of the new value.
    Only non-detached (active) aliases can suppress auto-move.
    """
    aliases = source_aliases.get(name)
    if aliases is None:
        return False
    active = aliases - detached_aliases
    return bool(active & live)


def _compute_alias_detachment(
    stmts: list[TpyStmt],
    source_aliases: _Aliases,
    alias_sources: dict[str, str],
) -> tuple[set[str], dict[str, int]]:
    """Compute per-alias detachment and source reassignment positions.

    Returns (detached_aliases, first_reassign_pos).
    An alias is "detached" if it was created before the first reassignment
    of its source in the same block -- it points to old storage and doesn't
    constrain moves of the new source value.

    Only scans direct children (not nested in if/for/etc). Conditional
    reassignments conservatively keep alias checking active.
    """
    first_reassign_pos: dict[str, int] = {}
    alias_creation_pos: dict[str, int] = {}
    seen: set[str] = set()
    for i, stmt in enumerate(stmts):
        if not isinstance(stmt, TpyVarDecl):
            continue
        name = stmt.name
        # Track source reassignment positions (second+ TpyVarDecl = reassign)
        if name in source_aliases:
            if name in seen:
                if name not in first_reassign_pos:
                    first_reassign_pos[name] = i
            else:
                seen.add(name)
        # Track alias creation positions
        if name in alias_sources and name not in alias_creation_pos:
            alias_creation_pos[name] = i
    # An alias is detached only if created BEFORE its source's first reassignment
    detached: set[str] = set()
    for alias, source in alias_sources.items():
        if source in first_reassign_pos and alias in alias_creation_pos:
            if alias_creation_pos[alias] < first_reassign_pos[source]:
                detached.add(alias)
    return detached, first_reassign_pos


# -- Termination check --------------------------------------------------------

def while_head_always_true(stmt: TpyWhile) -> bool:
    """Whether the head can never be false -- `while True:`.

    Distinct from "the head is true on entry", which the value ranges can
    prove for `i = 0; while i < 3` and which the body may then falsify: this
    one holds on EVERY evaluation, so the loop has no normal exit at all.
    Nothing falls out of its head, which is why its `else` clause never runs
    and why the statement after it is reached only from a `break`.
    """
    return (isinstance(stmt.condition, TpyBoolLiteral)
            and stmt.condition.value is True)


def stmts_terminate(stmts: list[TpyStmt]) -> bool:
    """Do all execution paths through stmts end with return/break/raise?

    Only inspects the last statement -- relies on the invariant that the
    parser does not emit unreachable statements after a terminator.
    TpyContinue is intentionally excluded: it goes back to the loop header,
    so variables may still be live in subsequent iterations.
    """
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (TpyReturn, TpyBreak, TpyRaise)):
        return True
    if isinstance(last, TpyIf):
        return (stmts_terminate(last.then_body)
                and stmts_terminate(last.else_body))
    if isinstance(last, TpyMatch):
        # A non-exhaustive match can fall through with no arm taken, so
        # all-arms-terminate alone is not termination.
        return (bool(last.cases)
                and last.is_exhaustive
                and all(stmts_terminate(case.body) for case in last.cases))
    if isinstance(last, TpyTry):
        # A finally body that itself terminates (return/raise) runs last on
        # every exit path, so the whole statement terminates regardless of
        # the try body.
        if last.finally_body and stmts_terminate(last.finally_body):
            return True
        return try_terminates_ignoring_finally(last)
    if isinstance(last, TpyWith):
        # A suppressing __exit__ can swallow body-raised exceptions and
        # fall through past the `with`, so body-terminates only implies
        # with-terminates when every __exit__ returns None.
        if any(item.exit_can_suppress for item in last.items):
            return False
        return stmts_terminate(last.body)
    if isinstance(last, TpyWhile):
        # `while True:` with no break targeting this loop never falls
        # through (it returns/raises from inside or runs forever).
        return (while_head_always_true(last)
                and not _has_loop_break(last.body))
    if isinstance(last, TpyAssert):
        # `assert False` lowers to an unconditional raise (TPy asserts are
        # never compiled out), so it terminates like a raise statement.
        return (isinstance(last.condition, TpyBoolLiteral)
                and last.condition.value is False)
    return False


def try_terminates_ignoring_finally(stmt: TpyTry) -> bool:
    """Do all paths through the try/handlers terminate, the finally aside?

    Distinct from asking `stmts_terminate` about the whole statement: that
    folds in the finally's OWN termination, since the finally runs last on
    every exit path. Codegen's normal-path finally emission needs the
    opposite question -- can control reach the end of the try/handlers, so
    that a fall-through copy of the finally is still needed? Answering it
    with the whole-statement fact elides the fall-through copy whenever the
    finally always raises/returns, and the finally never runs.
    """
    # try-finally only (no handlers): try-body terminating is enough.
    # The finally re-throws on exception.
    if not stmt.handlers:
        return stmts_terminate(stmt.try_body)
    # try with handlers: terminates iff try-body terminates AND every
    # handler body terminates. else-body (Python try-else) runs when
    # the try body completed normally; if try terminates, else is dead.
    return (stmts_terminate(stmt.try_body)
            and all(stmts_terminate(h.body) for h in stmt.handlers))


def _has_loop_break(stmts: list[TpyStmt]) -> bool:
    """Any break in `stmts` that would target the enclosing loop (does not
    descend into nested loops, whose breaks target themselves)."""
    for stmt in stmts:
        if isinstance(stmt, TpyBreak):
            return True
        if isinstance(stmt, (TpyWhile, TpyForEach)):
            # Breaks inside a nested loop's body target that loop, but its
            # else clause runs outside it -- a break there targets ours.
            if _has_loop_break(stmt.orelse):
                return True
            continue
        for body in stmt.sub_bodies():
            if _has_loop_break(body):
                return True
    return False


# -- Backward analysis --------------------------------------------------------

def _analyze_stmts_backward(
    stmts: list[TpyStmt],
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
    first_reassign_pos: dict[str, int] | None = None,
) -> None:
    """Walk statements backward, updating live set and marking last uses."""
    for i in range(len(stmts) - 1, -1, -1):
        stmt = stmts[i]
        # At the first reassignment of an alias source (going backward),
        # re-activate its aliases for earlier code (before reassignment,
        # aliases DO track the source and must constrain moves).
        if (first_reassign_pos and isinstance(stmt, TpyVarDecl)
                and first_reassign_pos.get(stmt.name) == i):
            aliases = source_aliases.get(stmt.name)
            if aliases:
                detached_aliases -= aliases
        _analyze_stmt(stmt, live, last_uses, source_aliases, detached_aliases)


def _analyze_stmt(
    stmt: TpyStmt,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze a single statement for last uses."""

    if isinstance(stmt, TpyIf):
        _analyze_if(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyMatch):
        _analyze_match(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyWhile):
        _analyze_while(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyForEach):
        _analyze_for_each(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyVarDecl):
        # `live` here is liveness AFTER the statement -- what the alias-rebind
        # check asks of a rebind site, and the same question _analyze_with
        # answers for `target_read_after`. Stamped before the kill below.
        stmt.live_names_after = frozenset(live)
        # Reads from the init expression
        if stmt.init:
            # For alias creation (alias = source), temporarily hide the alias
            # from the live set so it doesn't suppress the source's last-use.
            # The alias doesn't exist yet at this point in execution -- it's
            # being created by this statement -- so it can't constrain moves.
            hide_alias = (isinstance(stmt.init, TpyName)
                          and stmt.name != stmt.init.name
                          and stmt.name in live)
            if hide_alias:
                live.discard(stmt.name)
            _process_reads(stmt.init, live, last_uses, source_aliases, detached_aliases)
            if hide_alias:
                live.add(stmt.name)
        # Kill: variable is (re)defined here -- unless this statement's own
        # init reads it (`g = g.next()`), where the transfer is
        # (live_after - {g}) | {g} and g stays live entering the statement.
        _kill_unless_read(stmt.name, [stmt.init] if stmt.init else [], live)

    elif isinstance(stmt, TpyTupleUnpack):
        _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)
        for name in stmt.targets:
            if name is not None:
                _kill_unless_read(name, [stmt.value], live)

    elif isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyName):
            stmt.live_names_after = frozenset(live)
        # Value and target sub-expression reads form one C++ full-expression;
        # process them together so the multi-occurrence suppression sees both.
        exprs: list[TpyExpr] = [stmt.value]
        if isinstance(stmt.target, TpySubscript):
            exprs.append(stmt.target.obj)
            exprs.append(stmt.target.index)
        elif isinstance(stmt.target, TpyFieldAccess):
            exprs.append(stmt.target.obj)
        _process_reads_multi(exprs, live, last_uses, source_aliases, detached_aliases)
        # Kill: if target is a plain name, it's redefined -- unless the
        # statement reads it too (see _kill_unless_read).
        if isinstance(stmt.target, TpyName):
            _kill_unless_read(stmt.target.name, exprs, live)

    elif isinstance(stmt, TpyAugAssign):
        # AugAssign (e.g. x += 1) reads the target AND the value
        exprs = [stmt.value]
        if isinstance(stmt.target, TpySubscript):
            exprs.append(stmt.target.obj)
            exprs.append(stmt.target.index)
        elif isinstance(stmt.target, TpyFieldAccess):
            exprs.append(stmt.target.obj)
        elif isinstance(stmt.target, TpyName):
            # x += val reads x, then writes x
            exprs.append(stmt.target)
            # Don't kill: the read happens before the write in the same stmt,
            # and the prescan handles aug-assign separately.
        _process_reads_multi(exprs, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyReturn):
        # Terminating: nothing reached after the return is live. Clear first
        # so the return-expression reads become the only live-before names --
        # otherwise an earlier consume of a var read here is misread as
        # last-use (clearing after would discard those reads).
        live.clear()
        if stmt.value:
            _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyRaise):
        # Terminating (transfers to a handler / unwinds). Clear normal-flow
        # live, then process the raised exception's own reads so an earlier
        # consume of a var read here is not misread as last-use.
        live.clear()
        _process_reads_multi(
            list(stmt.exprs()), live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyWith):
        _analyze_with(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyTry):
        _analyze_try(stmt, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyDelVar):
        for name in stmt.names:
            live.discard(name)

    elif isinstance(stmt, TpyNestedDef):
        # Captured vars are referenced by the closure (by-ref or by-value).
        # They must stay live so earlier uses aren't incorrectly marked as
        # last-use (which would cause std::move before the capture).
        live |= _nested_def_captures(stmt)
        live.discard(stmt.func.name)

    else:
        # Non-terminating, non-defining statements (yield, expr-stmt, assert,
        # del-item, del-attr, ...): process every read-bearing child expression
        # in one pass (a statement is one C++ full-expression for sequencing).
        # Routing through exprs() means a future read-bearing statement is
        # covered automatically rather than silently dropping its reads.
        _process_reads_multi(
            list(stmt.exprs()), live, last_uses, source_aliases, detached_aliases)


def _analyze_if(
    stmt: TpyIf,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze if/else with branch merging."""
    then_terminates = stmts_terminate(stmt.then_body)
    else_terminates = stmts_terminate(stmt.else_body)

    # If a branch terminates, post-if code is unreachable on that path --
    # start with empty live set instead of inheriting post-if liveness.
    live_then = set() if then_terminates else live.copy()
    _analyze_stmts_backward(stmt.then_body, live_then, last_uses, source_aliases, detached_aliases)

    live_else = set() if else_terminates else live.copy()
    _analyze_stmts_backward(stmt.else_body, live_else, last_uses, source_aliases, detached_aliases)

    # After both branches: union (conservative -- live if used in either path)
    live.clear()
    live.update(live_then)
    live.update(live_else)

    # Process condition reads (evaluated before either branch)
    _process_reads(stmt.condition, live, last_uses, source_aliases, detached_aliases)


def _analyze_match(
    stmt: TpyMatch,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze match/case with per-arm branch merging (same as if/else)."""
    # Case patterns are not read-tracked: value patterns (`case X.Y:`) reference
    # module-level constants, never movable locals, so they carry no last-use.
    merged_live: set[str] = set()
    for case in stmt.cases:
        arm_terminates = stmts_terminate(case.body)
        arm_live = set() if arm_terminates else live.copy()
        _analyze_stmts_backward(case.body, arm_live, last_uses, source_aliases, detached_aliases)
        # Guard expression reads
        if case.guard is not None:
            _process_reads(case.guard, arm_live, last_uses, source_aliases, detached_aliases)
        merged_live.update(arm_live)

    live.clear()
    live.update(merged_live)
    # Subject expression reads
    _process_reads(stmt.subject, live, last_uses, source_aliases, detached_aliases)


def _analyze_while(
    stmt: TpyWhile,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze while loop with fixpoint iteration."""
    # Analyze else block first (runs after loop, before subsequent code)
    if stmt.orelse:
        _analyze_stmts_backward(stmt.orelse, live, last_uses, source_aliases, detached_aliases)

    # Fixpoint: variables used in the loop body are live across iterations.
    # Iterate until the live set stabilizes.
    post_loop_live = live.copy()

    for _ in range(4):
        prev_live = live.copy()

        # Walk body backward to compute live set (marking disabled)
        body_live = live.copy()
        _compute_live_only(stmt.body, body_live)

        # Condition reads contribute to liveness
        for node in _collect_reads_expr(stmt.condition):
            body_live.add(node.name)

        # Loop-back: names live at body start are also live at body end
        live.clear()
        live.update(body_live | post_loop_live)

        if live == prev_live:
            break

    # Final marking pass with stabilized live set
    _analyze_stmts_backward(stmt.body, live, last_uses, source_aliases, detached_aliases)

    # Process condition reads
    _process_reads(stmt.condition, live, last_uses, source_aliases, detached_aliases)


def _analyze_for_each(
    stmt: TpyForEach,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze for-each loop with fixpoint iteration."""
    # Analyze else block first (runs after loop, before subsequent code)
    if stmt.orelse:
        _analyze_stmts_backward(stmt.orelse, live, last_uses, source_aliases, detached_aliases)

    post_loop_live = live.copy()

    for _ in range(4):
        prev_live = live.copy()

        # Walk body backward to compute live set
        body_live = live.copy()
        _compute_live_only(stmt.body, body_live)

        # Loop variable is killed at loop header (reassigned each iteration)
        body_live.discard(stmt.var)

        # Loop-back: names live at body start are also live at body end
        live.clear()
        live.update(body_live | post_loop_live)

        if live == prev_live:
            break

    # Final marking pass with stabilized live set
    _analyze_stmts_backward(stmt.body, live, last_uses, source_aliases, detached_aliases)

    # Kill the loop variable (assigned by the loop at each iteration)
    live.discard(stmt.var)

    # Process iterable reads
    _process_reads(stmt.iterable, live, last_uses, source_aliases, detached_aliases)


def _all_read_names(stmts: list[TpyStmt]) -> list[TpyName]:
    """May over-collect defined names (e.g. a plain assign target renders as a
    TpyName); callers only consult node identity against `last_uses`, where
    those surplus nodes never appear, so the surplus is harmless.
    """
    result: list[TpyName] = []
    for stmt in stmts:
        for expr in stmt.exprs():
            result.extend(_collect_reads_expr(expr))
        for body in stmt.sub_bodies():
            result.extend(_all_read_names(body))
    return result


def collect_finally_return_candidates(stmts: list[TpyStmt]) -> IdentitySet:
    """The TpyName nodes that are the direct `return <name>` value inside
    a try with a non-suspending finally -- EVERY such return, regardless of
    whether the finally body mentions the name. The finally can reach the
    local's storage through channels no syntactic read-scan can enumerate
    (aliases, closures, Ptr), and deferring an untouched local is
    semantically identical to the eager move -- so candidacy is structural,
    not read-based; only the suspending-finally subtree is excluded (the
    CFG pending-return slot has no deferred-capture recipe, and a stamp
    there would silently accept shapes that must keep their loud
    diagnostics).

    Such a read is consumable at the return site despite any later finally
    access: on the return path codegen defers the materialization until
    after the inline finally chain, and on the exception path the return
    never executed. Sema consults this set to keep the auto-move mark
    (which the exception-path discard in _analyze_try would otherwise drop)
    and to stamp TpyReturn.finally_deferred_capture for eligible
    reference-type shapes.
    """
    out: IdentitySet = IdentitySet()
    rebound = collect_nested_def_nonlocal_rebinds(stmts)
    _walk_finally_returns(stmts, 0, out, rebound, IdentitySet(),
                          suppressed=False)
    return out


def collect_finally_rebound_returns(stmts: list[TpyStmt]) -> IdentitySet:
    """The returned name members that a finally REBINDS -- the ones kept on
    the eager capture, which copies them before the chain."""
    kept: IdentitySet = IdentitySet()
    rebound = collect_nested_def_nonlocal_rebinds(stmts)
    _walk_finally_returns(stmts, 0, IdentitySet(), rebound, kept,
                          suppressed=False)
    return kept


def collect_nested_def_nonlocal_rebinds(stmts: list[TpyStmt], *,
                                        include_del: bool = False) -> set[str]:
    """Nonlocal names a nested def REBINDS (assigns the name itself, not a
    field). Such a rebind overwrites the outer local's storage in place (the
    reassigned-var scan does not see nested-def writes, so the local has no
    slot indirection -- BUGS.md), which would clobber a deferred return's
    borrow; those names keep the eager capture. Mutation-only nonlocal use
    (`b.n += 1`) does NOT exclude -- that is the aliasing deferral exists
    for. Name-level aug-assign counts as a rebind conservatively.
    `include_del` also counts a nested `nonlocal x; del x`; the finally
    return candidacy leaves it out, since its own closure-del guard rejects
    that shape.
    """
    rebound: set[str] = set()

    def scan_def(func: TpyFunction) -> None:
        nonlocals: set[str] = set()
        assigned: set[str] = set()

        def walk(body: list[TpyStmt]) -> None:
            for s in body:
                if isinstance(s, TpyNonlocal):
                    nonlocals.update(s.names)
                elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
                    assigned.add(s.target.name)
                elif isinstance(s, TpyVarDecl):
                    assigned.add(s.name)
                elif isinstance(s, TpyAugAssign) and isinstance(s.target, TpyName):
                    assigned.add(s.target.name)
                elif isinstance(s, TpyTupleUnpack):
                    assigned.update(n for n in s.targets if n is not None)
                elif isinstance(s, TpyForEach):
                    assigned.add(s.var)
                elif include_del and isinstance(s, TpyDelVar):
                    assigned.update(s.names)
                for b in s.sub_bodies():
                    walk(b)

        walk(func.body)
        rebound.update(nonlocals & assigned)

    def find_defs(body: list[TpyStmt]) -> None:
        for s in body:
            if isinstance(s, TpyNestedDef):
                scan_def(s.func)
            for b in s.sub_bodies():
                find_defs(b)

    find_defs(stmts)
    return rebound


def collect_deleted_names(stmts: list[TpyStmt]) -> set[str]:
    """Names a `del` in this body (not a nested def's) unbinds."""
    out: set[str] = set()

    def walk(body: list[TpyStmt]) -> None:
        for s in body:
            if isinstance(s, TpyDelVar):
                out.update(s.names)
            if isinstance(s, TpyNestedDef):
                continue
            for b in s.sub_bodies():
                walk(b)

    walk(stmts)
    return out


def tuple_literal_leaves(expr: TpyExpr, path: tuple[int, ...] = ()
                         ) -> 'list[tuple[tuple[int, ...], TpyExpr]]':
    """The non-tuple members of a (possibly nested) tuple literal with their
    index paths, left to right -- the order the literal evaluates them."""
    if not isinstance(expr, TpyTupleLiteral):
        return [(path, expr)]
    out: list[tuple[tuple[int, ...], TpyExpr]] = []
    for i, e in enumerate(expr.elements):
        out.extend(tuple_literal_leaves(e, path + (i,)))
    return out


def _finally_rebinds(body: list[TpyStmt]) -> set[str]:
    """Names a finally body rebinds at name level (a nested def's body is its
    own scope). A deferred return aliases the local's storage, so a rebind
    there would overwrite the pending object in place -- CPython's pending
    return keeps the object it named -- and such a name keeps the eager
    capture. `del` stays out: it is rejected against a deferred return."""
    out: set[str] = set()
    for s in body:
        names = written_names(s) if not isinstance(s, TpyDelVar) else set()
        if isinstance(s, TpyAugAssign) and isinstance(s.target, TpyName):
            # Left to sema: only it knows whether the operator updates in
            # place or rebinds (`__iadd__` vs `__add__`).
            names.discard(s.target.name)
        out |= names
        if isinstance(s, TpyNestedDef):
            continue
        for b in s.sub_bodies():
            out |= _finally_rebinds(b)
    return out


def _walk_finally_returns(stmts: list[TpyStmt], finally_depth: int,
                          out: IdentitySet, rebound: set[str],
                          kept: IdentitySet, *, suppressed: bool) -> None:
    for stmt in stmts:
        if isinstance(stmt, TpyReturn):
            if not suppressed and finally_depth > 0:
                # A returned tuple literal's name members are the same
                # pending-return aliases, one per element.
                for _, leaf in tuple_literal_leaves(stmt.value):
                    if not isinstance(leaf, TpyName):
                        continue
                    if leaf.name in rebound:
                        kept.add(leaf)
                    else:
                        out.add(leaf)
        elif isinstance(stmt, TpyNestedDef):
            # A nested def's returns exit the inner function; the enclosing
            # finallies never run for them. Its own analysis pass covers it.
            continue
        elif isinstance(stmt, TpyTry) and stmt.finally_body:
            sub_suppressed = (suppressed
                              or stmts_have_any_suspension(stmt.finally_body))
            inner = rebound | _finally_rebinds(stmt.finally_body)
            _walk_finally_returns(stmt.try_body, finally_depth + 1, out,
                                  inner, kept, suppressed=sub_suppressed)
            for h in stmt.handlers:
                _walk_finally_returns(h.body, finally_depth + 1, out,
                                      inner, kept, suppressed=sub_suppressed)
            _walk_finally_returns(stmt.else_body, finally_depth + 1, out,
                                  inner, kept, suppressed=sub_suppressed)
            # A return in the finally body itself overrides at chain position
            # (no deferral); only outer finallies apply to it.
            _walk_finally_returns(stmt.finally_body, finally_depth, out,
                                  rebound, kept, suppressed=sub_suppressed)
        else:
            for body in stmt.sub_bodies():
                _walk_finally_returns(body, finally_depth, out,
                                      rebound, kept, suppressed=suppressed)


def _analyze_with(
    stmt: TpyWith,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze a with statement. `__exit__` runs after the body on every path
    and reads the context manager, so the manager's root names are live
    across the whole body -- a consume of the manager inside its own body
    must never be a last use (the epilogue would call `__exit__` on a
    moved-from object). The body is otherwise a plain sequential block:
    recurse it, kill the `as` targets (bound at entry), then process the
    context-manager expressions (evaluated at entry).

    `live` on entry is liveness AFTER the statement, which is exactly the
    question `TpyWithItem.target_read_after` asks, so stamp it here rather
    than re-deriving it from source order: this walk already knows that a
    closure capture keeps a name live everywhere, that a loop body runs
    again, that an exception can leave the try mid-body, and that a rebind
    only kills from its own position.
    """
    for item in stmt.items:
        if item.target is not None:
            item.target_read_after = item.target in live
    mgr_roots: set[str] = set()
    for item in stmt.items:
        for node in _collect_reads_expr(item.context_expr):
            mgr_roots.add(node.name)
    live |= mgr_roots
    _analyze_stmts_backward(stmt.body, live, last_uses, source_aliases, detached_aliases)
    # A body-internal kill (reassignment of the manager var) drops the seed
    # above; retract any marks the body walk still handed to manager reads --
    # __exit__ reads the manager on every path, so none can be a last use.
    if mgr_roots:
        for node in _all_read_names(stmt.body):
            if node.name in mgr_roots:
                last_uses.discard(node)
    for item in reversed(stmt.items):
        if item.target is not None:
            live.discard(item.target)
        _process_reads(item.context_expr, live, last_uses, source_aliases, detached_aliases)


def _analyze_try(
    stmt: TpyTry,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze a try statement.

    finally runs last on every path; handlers run on the exception path; else
    runs on the normal path after the try body. An exception can transfer to a
    handler at ANY point in the try body, so a name read by a handler or by
    finally is live across the whole try body and must never be marked
    last-use there -- otherwise a consume in the try body could move a value
    the exception path still reads. We compute the handler/finally/else live
    sets, mark the try body normally, then drop the last-use marks the body
    walk gave to any name read on an exception path.

    That same reachability makes the exception path's names live ENTERING the
    statement, so they are restored afterwards: a terminator in the try body
    clears the live set (nothing follows it on the normal path), which would
    otherwise drop the handler/finally seed and report a name read only on the
    exception path as dead before the try.
    """
    finally_live = live.copy()
    if stmt.finally_body:
        _analyze_stmts_backward(stmt.finally_body, finally_live, last_uses, source_aliases, detached_aliases)

    handler_union: set[str] = set()
    for h in stmt.handlers:
        h_live = finally_live.copy()
        _analyze_stmts_backward(h.body, h_live, last_uses, source_aliases, detached_aliases)
        if h.binding is not None:
            h_live.discard(h.binding)
        handler_union |= h_live

    else_live = finally_live.copy()
    if stmt.else_body:
        _analyze_stmts_backward(stmt.else_body, else_live, last_uses, source_aliases, detached_aliases)

    # Try body: normal exit flows to else, exception at any point to a handler.
    live.clear()
    live.update(else_live | handler_union)
    _analyze_stmts_backward(stmt.try_body, live, last_uses, source_aliases, detached_aliases)

    exception_path_live = handler_union | finally_live
    if exception_path_live:
        for node in _all_read_names(stmt.try_body):
            if node.name in exception_path_live:
                last_uses.discard(node)
    live |= exception_path_live


def _compute_live_only(stmts: list[TpyStmt], live: set[str]) -> None:
    """Walk statements backward, updating live set WITHOUT marking last uses.

    Used during fixpoint iteration to stabilize the live set before
    doing the actual marking pass.
    """
    for stmt in reversed(stmts):
        _compute_stmt_live_only(stmt, live)


def _compute_stmt_live_only(stmt: TpyStmt, live: set[str]) -> None:
    """Update live set for a statement without marking last uses."""

    if isinstance(stmt, TpyIf):
        then_terminates = stmts_terminate(stmt.then_body)
        else_terminates = stmts_terminate(stmt.else_body)
        live_then = set() if then_terminates else live.copy()
        _compute_live_only(stmt.then_body, live_then)
        live_else = set() if else_terminates else live.copy()
        _compute_live_only(stmt.else_body, live_else)
        live.clear()
        live.update(live_then | live_else)
        for node in _collect_reads_expr(stmt.condition):
            live.add(node.name)

    elif isinstance(stmt, TpyMatch):
        merged: set[str] = set()
        for case in stmt.cases:
            arm_terminates = stmts_terminate(case.body)
            arm_live = set() if arm_terminates else live.copy()
            _compute_live_only(case.body, arm_live)
            if case.guard is not None:
                for node in _collect_reads_expr(case.guard):
                    arm_live.add(node.name)
            merged.update(arm_live)
        live.clear()
        live.update(merged)
        for node in _collect_reads_expr(stmt.subject):
            live.add(node.name)

    elif isinstance(stmt, TpyWhile):
        # Approximate: collect all reads in body + condition + orelse
        if stmt.orelse:
            _compute_live_only(stmt.orelse, live)
        for node in _collect_reads_expr(stmt.condition):
            live.add(node.name)
        body_live = live.copy()
        _compute_live_only(stmt.body, body_live)
        live.update(body_live)

    elif isinstance(stmt, TpyForEach):
        if stmt.orelse:
            _compute_live_only(stmt.orelse, live)
        for node in _collect_reads_expr(stmt.iterable):
            live.add(node.name)
        body_live = live.copy()
        _compute_live_only(stmt.body, body_live)
        body_live.discard(stmt.var)
        live.update(body_live)

    elif isinstance(stmt, TpyVarDecl):
        # No hide_alias here (unlike _analyze_stmt) -- conservative for loop
        # fixpoint: prevents move-through inside loop bodies where the source
        # may be live from prior iterations.
        if stmt.init:
            for node in _collect_reads_expr(stmt.init):
                live.add(node.name)
        _kill_unless_read(stmt.name, [stmt.init] if stmt.init else [], live)

    elif isinstance(stmt, TpyTupleUnpack):
        for node in _collect_reads_expr(stmt.value):
            live.add(node.name)
        for name in stmt.targets:
            if name is not None:
                _kill_unless_read(name, [stmt.value], live)

    elif isinstance(stmt, TpyAssign):
        for node in _collect_reads_expr(stmt.value):
            live.add(node.name)
        if isinstance(stmt.target, TpySubscript):
            for node in _collect_reads_expr(stmt.target.obj):
                live.add(node.name)
            for node in _collect_reads_expr(stmt.target.index):
                live.add(node.name)
        elif isinstance(stmt.target, TpyFieldAccess):
            for node in _collect_reads_expr(stmt.target.obj):
                live.add(node.name)
        elif isinstance(stmt.target, TpyName):
            _kill_unless_read(stmt.target.name, [stmt.value], live)

    elif isinstance(stmt, TpyAugAssign):
        for node in _collect_reads_expr(stmt.value):
            live.add(node.name)
        if isinstance(stmt.target, TpySubscript):
            for node in _collect_reads_expr(stmt.target.obj):
                live.add(node.name)
            for node in _collect_reads_expr(stmt.target.index):
                live.add(node.name)
        elif isinstance(stmt.target, TpyFieldAccess):
            for node in _collect_reads_expr(stmt.target.obj):
                live.add(node.name)
        elif isinstance(stmt.target, TpyName):
            live.add(stmt.target.name)

    elif isinstance(stmt, TpyReturn):
        # Clear before adding reads: the return terminates this path, so only
        # the return-expression reads are live-before (clearing after would
        # discard them).
        live.clear()
        if stmt.value:
            for node in _collect_reads_expr(stmt.value):
                live.add(node.name)

    elif isinstance(stmt, TpyRaise):
        live.clear()
        for expr in stmt.exprs():
            for node in _collect_reads_expr(expr):
                live.add(node.name)

    elif isinstance(stmt, TpyWith):
        # Manager roots stay live across the body (__exit__ reads them) --
        # mirrors _analyze_with's seed.
        for item in stmt.items:
            for node in _collect_reads_expr(item.context_expr):
                live.add(node.name)
        _compute_live_only(stmt.body, live)
        for item in reversed(stmt.items):
            if item.target is not None:
                live.discard(item.target)
            for node in _collect_reads_expr(item.context_expr):
                live.add(node.name)

    elif isinstance(stmt, TpyTry):
        # Conservative over-approximation: any sub-body's reads may be live
        # entering the try (an exception can transfer mid-body to a handler).
        if stmt.finally_body:
            _compute_live_only(stmt.finally_body, live)
        for h in stmt.handlers:
            h_live = live.copy()
            _compute_live_only(h.body, h_live)
            if h.binding is not None:
                h_live.discard(h.binding)
            live.update(h_live)
        if stmt.else_body:
            _compute_live_only(stmt.else_body, live)
        # Restored after the body walk for the same reason as in _analyze_try:
        # a terminator in the try body clears the set, which would drop the
        # handler/finally reads that stay live entering the statement.
        exception_path_live = live.copy()
        _compute_live_only(stmt.try_body, live)
        live |= exception_path_live

    elif isinstance(stmt, TpyDelVar):
        for name in stmt.names:
            live.discard(name)

    elif isinstance(stmt, TpyNestedDef):
        live |= _nested_def_captures(stmt)
        live.discard(stmt.func.name)

    else:
        # yield, expr-stmt, assert, del-item, del-attr, ...: add child-expr reads.
        for expr in stmt.exprs():
            for node in _collect_reads_expr(expr):
                live.add(node.name)


# -- Read collection ----------------------------------------------------------

def _kill_unless_read(name: str, read_exprs: list[TpyExpr],
                      live: set[str]) -> None:
    """Apply a definition's kill of `name`, honouring the reads in the same
    statement: the backward transfer is (live_after - kills) | reads, so a
    self-referential definition (`g = g.next()`, `a, b = b, a`) leaves the
    name live entering the statement.

    Marking still happens against live_after (the reads are processed before
    this call), so which sites auto-move is unchanged -- only what the
    statements above see.
    """
    if any(n.name == name
           for expr in read_exprs
           for n in _collect_reads_expr(expr)):
        return
    live.discard(name)


def _process_reads(
    expr: TpyExpr,
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Collect name reads in an expression, mark last uses, update live set."""
    _process_reads_multi([expr], live, last_uses, source_aliases, detached_aliases)


def _process_reads_multi(
    exprs: list[TpyExpr],
    live: set[str],
    last_uses: IdentitySet,
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Like _process_reads, but over all of one statement's expressions at
    once. The multi-occurrence suppression below must see every read the
    statement emits into a single C++ full-expression -- splitting value and
    target reads into separate calls let a consume in the value be marked
    last-use while the target read of the same variable was sequenced
    indeterminately around it (e.g. `d[len(b.items)] = k.take(b)`).
    """
    walrus_defs: set[str] = set()
    reads: list[TpyName] = []
    for expr in exprs:
        reads.extend(_collect_reads_expr(expr, walrus_defs))
    if not reads and not walrus_defs:
        return

    # Count occurrences per variable name in this expression.
    # If a variable appears multiple times, don't mark any as last use
    # (C++ argument evaluation order is unspecified).
    name_counts: dict[str, int] = {}
    for node in reads:
        name_counts[node.name] = name_counts.get(node.name, 0) + 1

    # Mark single-occurrence reads that are not live (not used later)
    # and have no live T& aliases (prevents dangling references)
    for node in reads:
        if (name_counts[node.name] == 1
                and node.name not in live
                and not _has_live_alias(node.name, live, source_aliases, detached_aliases)):
            last_uses.add(node)

    # Add all read names to live set
    for node in reads:
        live.add(node.name)

    # Walrus targets are definitions -- kill them from live set
    live -= walrus_defs


def _collect_reads_expr(expr: TpyExpr,
                        walrus_defs: set[str] | None = None) -> list[TpyName]:
    """Collect TpyName nodes read (not defined) in an expression.

    Uses TpyExpr.children() for iterative traversal.
    If walrus_defs is provided, also records walrus (:=) target names into it
    (those are definitions, not reads, and are excluded from the returned list).
    """
    result: list[TpyName] = []
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, TpyName):
            result.append(node)
            continue
        if isinstance(node, TpyNamedExpr) and walrus_defs is not None:
            walrus_defs.add(node.target)
        stack.extend(node.children())
    return result
