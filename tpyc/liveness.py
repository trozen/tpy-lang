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
    TpyIf, TpyWhile, TpyForEach, TpyReturn, TpyBreak, TpyAssert,
    TpyExprStmt, TpyRaiseStopIteration,
    TpyName, TpyCall, TpyMethodCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp,
    TpyTypeParamConstruct,
    TpyFieldAccess, TpySubscript, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce, TpyIfExpr,
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

def _stmts_terminate(stmts: list[TpyStmt]) -> bool:
    """Do all execution paths through stmts end with return/break/raise?

    Only inspects the last statement -- relies on the invariant that the
    parser does not emit unreachable statements after a terminator.
    TpyContinue is intentionally excluded: it goes back to the loop header,
    so variables may still be live in subsequent iterations.
    """
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (TpyReturn, TpyBreak, TpyRaiseStopIteration)):
        return True
    if isinstance(last, TpyIf):
        return (_stmts_terminate(last.then_body)
                and _stmts_terminate(last.else_body))
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
        elif isinstance(stmt.target, TpyName):
            # x += val reads x, then writes x
            _process_reads(stmt.target, live, last_uses, source_aliases, detached_aliases)
            # Don't kill: the read happens before the write in the same stmt,
            # and the prescan handles aug-assign separately.

    elif isinstance(stmt, TpyReturn):
        if stmt.value:
            _process_reads(stmt.value, live, last_uses, source_aliases, detached_aliases)
        # After a return, nothing is live (this path terminates)
        live.clear()

    elif isinstance(stmt, TpyExprStmt):
        _process_reads(stmt.expr, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyAssert):
        _process_reads(stmt.condition, live, last_uses, source_aliases, detached_aliases)
        if stmt.message:
            _process_reads(stmt.message, live, last_uses, source_aliases, detached_aliases)

    elif isinstance(stmt, TpyRaiseStopIteration):
        # Terminates this path
        live.clear()

    # TpyBreak, TpyContinue, TpyPassStmt, TpyGlobal, TpyImport: no reads


def _analyze_if(
    stmt: TpyIf,
    live: set[str],
    last_uses: set[int],
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze if/else with branch merging."""
    then_terminates = _stmts_terminate(stmt.then_body)
    else_terminates = _stmts_terminate(stmt.else_body)

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


def _analyze_while(
    stmt: TpyWhile,
    live: set[str],
    last_uses: set[int],
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Analyze while loop with fixpoint iteration."""
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
        then_terminates = _stmts_terminate(stmt.then_body)
        else_terminates = _stmts_terminate(stmt.else_body)
        live_then = set() if then_terminates else live.copy()
        _compute_live_only(stmt.then_body, live_then)
        live_else = set() if else_terminates else live.copy()
        _compute_live_only(stmt.else_body, live_else)
        live.clear()
        live.update(live_then | live_else)
        for node in _collect_reads_expr(stmt.condition):
            live.add(node.name)

    elif isinstance(stmt, TpyWhile):
        # Approximate: collect all reads in body + condition
        for node in _collect_reads_expr(stmt.condition):
            live.add(node.name)
        body_live = live.copy()
        _compute_live_only(stmt.body, body_live)
        live.update(body_live)

    elif isinstance(stmt, TpyForEach):
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
        elif isinstance(stmt.target, TpyName):
            live.add(stmt.target.name)

    elif isinstance(stmt, TpyReturn):
        if stmt.value:
            for node in _collect_reads_expr(stmt.value):
                live.add(node.name)
        live.clear()

    elif isinstance(stmt, TpyExprStmt):
        for node in _collect_reads_expr(stmt.expr):
            live.add(node.name)

    elif isinstance(stmt, TpyAssert):
        for node in _collect_reads_expr(stmt.condition):
            live.add(node.name)
        if stmt.message:
            for node in _collect_reads_expr(stmt.message):
                live.add(node.name)

    elif isinstance(stmt, TpyRaiseStopIteration):
        live.clear()


# -- Read collection ----------------------------------------------------------

def _process_reads(
    expr: TpyExpr,
    live: set[str],
    last_uses: set[int],
    source_aliases: _Aliases,
    detached_aliases: set[str],
) -> None:
    """Collect name reads in an expression, mark last uses, update live set."""
    reads = _collect_reads_expr(expr)
    if not reads:
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


def _collect_reads_expr(expr: TpyExpr) -> list[TpyName]:
    """Recursively collect all TpyName nodes that are read in an expression."""
    if isinstance(expr, TpyName):
        return [expr]

    elif isinstance(expr, TpyCall):
        result: list[TpyName] = []
        for arg in expr.args:
            result.extend(_collect_reads_expr(arg))
        for kwarg in expr.kwargs.values():
            result.extend(_collect_reads_expr(kwarg))
        return result

    elif isinstance(expr, TpyMethodCall):
        result = _collect_reads_expr(expr.obj)
        for arg in expr.args:
            result.extend(_collect_reads_expr(arg))
        for kwarg in expr.kwargs.values():
            result.extend(_collect_reads_expr(kwarg))
        return result

    elif isinstance(expr, TpyBinOp):
        return _collect_reads_expr(expr.left) + _collect_reads_expr(expr.right)

    elif isinstance(expr, TpyChainedCompare):
        result = _collect_reads_expr(expr.left)
        for comp in expr.comparators:
            result.extend(_collect_reads_expr(comp))
        return result

    elif isinstance(expr, TpyUnaryOp):
        return _collect_reads_expr(expr.operand)

    elif isinstance(expr, TpyFieldAccess):
        return _collect_reads_expr(expr.obj)

    elif isinstance(expr, TpySubscript):
        return _collect_reads_expr(expr.obj) + _collect_reads_expr(expr.index)

    elif isinstance(expr, TpyArrayLiteral):
        result = []
        for elem in expr.elements:
            result.extend(_collect_reads_expr(elem))
        return result

    elif isinstance(expr, TpyListRepeat):
        result = []
        for elem in expr.elements:
            result.extend(_collect_reads_expr(elem))
        result.extend(_collect_reads_expr(expr.count))
        return result

    elif isinstance(expr, TpyCoerce):
        return _collect_reads_expr(expr.expr)

    elif isinstance(expr, TpyIfExpr):
        # Flat collection: a var in both branches counts >= 2 -> no move
        # (conservative but correct; only one branch evaluates at runtime).
        result = _collect_reads_expr(expr.condition)
        result.extend(_collect_reads_expr(expr.then_expr))
        result.extend(_collect_reads_expr(expr.else_expr))
        return result

    elif isinstance(expr, TpyTypeParamConstruct):
        return []

    # Literals (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    # TpyBoolLiteral, TpyNoneLiteral): no reads
    return []
