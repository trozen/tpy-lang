"""Last-use liveness analysis for auto-move optimization.

Pure AST walking -- no type registry or analysis context needed.
Run once per function/method in sema; results consumed by both sema and codegen.

The analysis identifies TpyName nodes that are the "last use" of a variable --
meaning the variable is not read again on any subsequent execution path (without
being reassigned first). These sites are candidates for std::move() in codegen.
"""

from __future__ import annotations

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyAssign, TpyAugAssign,
    TpyIf, TpyWhile, TpyForEach, TpyReturn, TpyAssert,
    TpyExprStmt, TpyRaiseStopIteration,
    TpyName, TpyCall, TpyMethodCall, TpyBinOp, TpyUnaryOp,
    TpyFieldAccess, TpySubscript, TpyArrayLiteral, TpyListRepeat,
    TpyCoerce,
)


def analyze_last_uses(stmts: list[TpyStmt]) -> set[int]:
    """Analyze a function body to find last-use sites for auto-move.

    Returns a set of id(TpyName) for name nodes that are at their last use --
    the variable is not read again on any subsequent execution path without
    being reassigned first.

    The analysis is conservative: if unsure, a node is NOT marked as last use.
    A missed optimization is just a copy (same as current behavior).
    """
    live: set[str] = set()
    last_uses: set[int] = set()
    _analyze_stmts_backward(stmts, live, last_uses)
    return last_uses


def _analyze_stmts_backward(
    stmts: list[TpyStmt],
    live: set[str],
    last_uses: set[int],
) -> None:
    """Walk statements backward, updating live set and marking last uses."""
    for stmt in reversed(stmts):
        _analyze_stmt(stmt, live, last_uses)


def _analyze_stmt(
    stmt: TpyStmt,
    live: set[str],
    last_uses: set[int],
) -> None:
    """Analyze a single statement for last uses."""

    if isinstance(stmt, TpyIf):
        _analyze_if(stmt, live, last_uses)

    elif isinstance(stmt, TpyWhile):
        _analyze_while(stmt, live, last_uses)

    elif isinstance(stmt, TpyForEach):
        _analyze_for_each(stmt, live, last_uses)

    elif isinstance(stmt, TpyVarDecl):
        # Reads from the init expression
        if stmt.init:
            _process_reads(stmt.init, live, last_uses)
        # Kill: variable is (re)defined here
        live.discard(stmt.name)

    elif isinstance(stmt, TpyAssign):
        # Reads from the value
        _process_reads(stmt.value, live, last_uses)
        # Reads from target sub-expressions (subscript index, field obj)
        if isinstance(stmt.target, TpySubscript):
            _process_reads(stmt.target.obj, live, last_uses)
            _process_reads(stmt.target.index, live, last_uses)
        elif isinstance(stmt.target, TpyFieldAccess):
            _process_reads(stmt.target.obj, live, last_uses)
        # Kill: if target is a plain name, it's redefined
        elif isinstance(stmt.target, TpyName):
            live.discard(stmt.target.name)

    elif isinstance(stmt, TpyAugAssign):
        # AugAssign (e.g. x += 1) reads the target AND the value
        _process_reads(stmt.value, live, last_uses)
        if isinstance(stmt.target, TpySubscript):
            _process_reads(stmt.target.obj, live, last_uses)
            _process_reads(stmt.target.index, live, last_uses)
        elif isinstance(stmt.target, TpyName):
            # x += val reads x, then writes x
            _process_reads(stmt.target, live, last_uses)
            # Don't kill: the read happens before the write in the same stmt,
            # and the prescan handles aug-assign separately.

    elif isinstance(stmt, TpyReturn):
        if stmt.value:
            _process_reads(stmt.value, live, last_uses)
        # After a return, nothing is live (this path terminates)
        live.clear()

    elif isinstance(stmt, TpyExprStmt):
        _process_reads(stmt.expr, live, last_uses)

    elif isinstance(stmt, TpyAssert):
        _process_reads(stmt.condition, live, last_uses)
        if stmt.message:
            _process_reads(stmt.message, live, last_uses)

    elif isinstance(stmt, TpyRaiseStopIteration):
        # Terminates this path
        live.clear()

    # TpyBreak, TpyContinue, TpyPassStmt, TpyGlobal, TpyImport: no reads


def _analyze_if(
    stmt: TpyIf,
    live: set[str],
    last_uses: set[int],
) -> None:
    """Analyze if/else with branch merging."""
    # Analyze then-branch with a copy of live
    live_then = live.copy()
    _analyze_stmts_backward(stmt.then_body, live_then, last_uses)

    # Analyze else-branch with a copy of live
    live_else = live.copy()
    _analyze_stmts_backward(stmt.else_body, live_else, last_uses)

    # After both branches: union (conservative -- live if used in either path)
    live.clear()
    live.update(live_then)
    live.update(live_else)

    # Process condition reads (evaluated before either branch)
    _process_reads(stmt.condition, live, last_uses)


def _analyze_while(
    stmt: TpyWhile,
    live: set[str],
    last_uses: set[int],
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
    _analyze_stmts_backward(stmt.body, live, last_uses)

    # Process condition reads
    _process_reads(stmt.condition, live, last_uses)


def _analyze_for_each(
    stmt: TpyForEach,
    live: set[str],
    last_uses: set[int],
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
    _analyze_stmts_backward(stmt.body, live, last_uses)

    # Kill the loop variable (assigned by the loop at each iteration)
    live.discard(stmt.var)

    # Process iterable reads
    _process_reads(stmt.iterable, live, last_uses)


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
        live_then = live.copy()
        _compute_live_only(stmt.then_body, live_then)
        live_else = live.copy()
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
        if stmt.init:
            for node in _collect_reads_expr(stmt.init):
                live.add(node.name)
        live.discard(stmt.name)

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
    for node in reads:
        if name_counts[node.name] == 1 and node.name not in live:
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
        return result

    elif isinstance(expr, TpyBinOp):
        return _collect_reads_expr(expr.left) + _collect_reads_expr(expr.right)

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

    # Literals (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    # TpyBoolLiteral, TpyNoneLiteral): no reads
    return []
