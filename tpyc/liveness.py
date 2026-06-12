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

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyReturn, TpyBreak, TpyRaise,
    TpyMatch, TpyNestedDef, TpyDelVar, TpyTry, TpyWith,
    TpyName, TpyFieldAccess, TpySubscript, TpyNamedExpr,
)

# source_name -> set[alias_name] reverse map
_Aliases = dict[str, set[str]]


def analyze_last_uses(
    stmts: list[TpyStmt],
    alias_sources: dict[str, str] | None = None,
) -> set[int]:
    """Analyze a function body to find last-use sites for auto-move.

    Returns a set of id(TpyName) for name nodes that are at their last use --
    the variable is not read again on any subsequent execution path without
    being reassigned first.

    When alias_sources is provided (alias_name -> source_name), auto-move of
    a source variable is suppressed if any of its T& aliases are still live.

    The analysis is conservative: if unsure, a node is NOT marked as last use.
    A missed optimization is just a copy (same as current behavior).
    """
    source_aliases = _build_source_aliases(alias_sources) if alias_sources else {}
    # Per-alias detachment: aliases created before their source's reassignment
    # point to old storage and don't constrain moves of the new value.
    # Re-activated at the reassignment point going backward.
    detached_aliases, first_reassign_pos = (
        _compute_alias_detachment(stmts, source_aliases, alias_sources)
        if alias_sources else (set(), {})
    )
    live: set[str] = set()
    last_uses: set[int] = set()
    _analyze_stmts_backward(
        stmts, live, last_uses, source_aliases,
        detached_aliases, first_reassign_pos,
    )
    return last_uses


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
        # try-finally only (no handlers): try-body terminating is enough.
        # The finally re-throws on exception; a finally body that itself
        # terminates only changes behavior if the try body fell through,
        # which we don't analyze here (conservative: ignore finally-body).
        if not last.handlers:
            return stmts_terminate(last.try_body)
        # try with handlers: terminates iff try-body terminates AND every
        # handler body terminates. else-body (Python try-else) runs when
        # the try body completed normally; if try terminates, else is dead.
        return (stmts_terminate(last.try_body)
                and all(stmts_terminate(h.body) for h in last.handlers))
    if isinstance(last, TpyWith):
        # A suppressing __exit__ can swallow body-raised exceptions and
        # fall through past the `with`, so body-terminates only implies
        # with-terminates when every __exit__ returns None.
        if any(item.exit_can_suppress for item in last.items):
            return False
        return stmts_terminate(last.body)
    return False


# -- Backward analysis --------------------------------------------------------

def _analyze_stmts_backward(
    stmts: list[TpyStmt],
    live: set[str],
    last_uses: set[int],
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
    last_uses: set[int],
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
        # Kill: variable is (re)defined here
        live.discard(stmt.name)

    elif isinstance(stmt, TpyTupleUnpack):
        _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)
        for name in stmt.targets:
            if name is not None:
                live.discard(name)

    elif isinstance(stmt, TpyAssign):
        # Reads from the value
        _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)
        # Reads from target sub-expressions (subscript index, field obj)
        if isinstance(stmt.target, TpySubscript):
            _process_reads(stmt.target.obj, live, last_uses, source_aliases, detached_aliases)
            _process_reads(stmt.target.index, live, last_uses, source_aliases, detached_aliases)
        elif isinstance(stmt.target, TpyFieldAccess):
            _process_reads(stmt.target.obj, live, last_uses, source_aliases, detached_aliases)
        # Kill: if target is a plain name, it's redefined
        elif isinstance(stmt.target, TpyName):
            live.discard(stmt.target.name)

    elif isinstance(stmt, TpyAugAssign):
        # AugAssign (e.g. x += 1) reads the target AND the value
        _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)
        if isinstance(stmt.target, TpySubscript):
            _process_reads(stmt.target.obj, live, last_uses, source_aliases, detached_aliases)
            _process_reads(stmt.target.index, live, last_uses, source_aliases, detached_aliases)
        elif isinstance(stmt.target, TpyFieldAccess):
            _process_reads(stmt.target.obj, live, last_uses, source_aliases, detached_aliases)
        elif isinstance(stmt.target, TpyName):
            # x += val reads x, then writes x
            _process_reads(stmt.target, live, last_uses, source_aliases, detached_aliases)
            # Don't kill: the read happens before the write in the same stmt,
            # and the prescan handles aug-assign separately.

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
        for expr in stmt.exprs():
            _process_reads(expr, live, last_uses, source_aliases, detached_aliases)

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
        if stmt.captured_names:
            for name in stmt.captured_names:
                live.add(name)
        live.discard(stmt.func.name)

    else:
        # Non-terminating, non-defining statements (yield, expr-stmt, assert,
        # del-item, del-attr, ...): process every read-bearing child expression.
        # Routing through exprs() means a future read-bearing statement is
        # covered automatically rather than silently dropping its reads.
        for expr in stmt.exprs():
            _process_reads(expr, live, last_uses, source_aliases, detached_aliases)


def _analyze_if(
    stmt: TpyIf,
    live: set[str],
    last_uses: set[int],
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
    last_uses: set[int],
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
    last_uses: set[int],
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
    last_uses: set[int],
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


def _analyze_with(
    stmt: TpyWith,
    live: set[str],
    last_uses: set[int],
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze a with statement. `__exit__` runs after the body on every path
    but reads only the context manager, not body locals, so the body is a
    plain sequential block: recurse it, kill the `as` targets (bound at
    entry), then process the context-manager expressions (evaluated at entry).
    """
    _analyze_stmts_backward(stmt.body, live, last_uses, source_aliases, detached_aliases)
    for item in reversed(stmt.items):
        if item.target is not None:
            live.discard(item.target)
        _process_reads(item.context_expr, live, last_uses, source_aliases, detached_aliases)


def _analyze_try(
    stmt: TpyTry,
    live: set[str],
    last_uses: set[int],
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
                last_uses.discard(id(node))


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
        live.discard(stmt.name)

    elif isinstance(stmt, TpyTupleUnpack):
        for node in _collect_reads_expr(stmt.value):
            live.add(node.name)
        for name in stmt.targets:
            if name is not None:
                live.discard(name)

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
            live.discard(stmt.target.name)

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
        _compute_live_only(stmt.try_body, live)

    elif isinstance(stmt, TpyDelVar):
        for name in stmt.names:
            live.discard(name)

    elif isinstance(stmt, TpyNestedDef):
        if stmt.captured_names:
            for name in stmt.captured_names:
                live.add(name)
        live.discard(stmt.func.name)

    else:
        # yield, expr-stmt, assert, del-item, del-attr, ...: add child-expr reads.
        for expr in stmt.exprs():
            for node in _collect_reads_expr(expr):
                live.add(node.name)


# -- Read collection ----------------------------------------------------------

def _process_reads(
    expr: TpyExpr,
    live: set[str],
    last_uses: set[int],
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Collect name reads in an expression, mark last uses, update live set."""
    walrus_defs: set[str] = set()
    reads = _collect_reads_expr(expr, walrus_defs)
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
            last_uses.add(id(node))

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
