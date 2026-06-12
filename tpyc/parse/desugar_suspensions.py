"""Pre-sema desugaring of suspensions out of conditional / repeated
expression positions.

`await` (and, when `send()` lands, yield-expressions) can only suspend at
statement position in the resumable-frame lowering. The codegen await-lifter
hoists a nested `await` into a *preceding* statement, which is correct only
when the await sits in an unconditional, evaluated-once position. In a
short-circuit operand (`a or await b()`), a ternary branch, a chained-compare
tail, or a `while` condition, that hoist changes evaluation order (skipped
operand runs anyway; loop condition runs once).

This pass rewrites those positions into statement-position suspensions guarded
by explicit control flow, so the lowered form preserves Python evaluation
order. It runs pre-sema (in the parser) so semantic analysis computes
narrowing / liveness / borrow / type facts on the rewritten control flow --
the synthetic temps and branches are ordinary statements by the time sema
sees them.

Pass-ownership boundary (two passes touch await placement -- keep it clean):
this pass owns ONLY the conditional / repeated positions (short-circuit
operands, ternary branches, chained-compare tails, `while` conditions),
which it rewrites into statement-position suspensions. Every other await --
bare, or in an unconditional evaluated-once position (assign RHS, call arg,
`if` / `assert` condition, `for` iterable) -- is left untouched for the
codegen await-lifter (`gen_async.py::_lift_nested_awaits`) to hoist. The two
passes must not both rewrite the same await: this pass leaves a lowered
condition/branch await at statement position precisely so the lifter's
unconditional hoist is then a no-op-or-safe on it.
"""

from dataclasses import fields, is_dataclass

from .nodes import (
    TpyStmt, TpyExpr, TpyName, TpyAwait, TpyBinOp, TpyIfExpr, TpyChainedCompare,
    TpyBoolLiteral, TpyAssign, TpyVarDecl, TpyIf, TpyWhile, TpyBreak,
    TpyMatch, TpyWith, TpyAssert, TpyUnaryOp, SourceLocation,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression, TpyFunction, ParseError,
)


def desugar_suspension_positions(func: TpyFunction) -> None:
    """Rewrite `func.body` in place so suspensions in conditional / repeated
    positions become statement-position suspensions. No-op for functions that
    cannot suspend."""
    if not func.is_async:
        return
    desugarer = _Desugarer()
    func.body = desugarer.body(func.body)


def _has_await(expr: TpyExpr | None) -> bool:
    """True if `expr` contains a TpyAwait anywhere in its own subtree.

    Does not descend into nested function/comprehension scopes -- a
    comprehension is handled by an explicit reject before this is consulted
    for its element, so we never need to look inside one here.
    """
    if expr is None:
        return False
    if isinstance(expr, TpyAwait):
        return True
    for child in expr.children():
        if _has_await(child):
            return True
    return False


class _Desugarer:
    def __init__(self) -> None:
        self._counter = 0

    def _fresh(self, label: str = "sc") -> str:
        # The label surfaces in diagnostics when the temp's type is rejected
        # (only the ternary temp can: its branch types may diverge, which TPy
        # reports as a reassignment mismatch on this name -- see _lower_ternary).
        name = f"__{label}_{self._counter}"
        self._counter += 1
        return name

    # -- statement level ------------------------------------------------------

    def body(self, stmts: list[TpyStmt]) -> list[TpyStmt]:
        out: list[TpyStmt] = []
        for stmt in stmts:
            out.extend(self._stmt(stmt))
        return out

    def _stmt(self, stmt: TpyStmt) -> list[TpyStmt]:
        # `while` is special: its condition is re-evaluated each iteration, so
        # the lowered prelude must live inside the loop, not before it.
        if isinstance(stmt, TpyWhile):
            return self._while(stmt)

        # `assert` is special: the message is evaluated only on failure (a
        # conditional position), so an awaiting message must not be hoisted
        # before the statement like a once-evaluated field.
        if isinstance(stmt, TpyAssert) and _has_await(stmt.message):
            return self._assert(stmt)

        # Recurse into compound sub-bodies first (innermost suspensions are
        # rewritten before the enclosing statement's own expressions).
        self._recurse_sub_bodies(stmt)

        # Lower this statement's own top-level expressions; any prelude runs
        # before the (once-evaluated) statement.
        prefix: list[TpyStmt] = []
        self._lower_stmt_exprs(stmt, prefix)
        return prefix + [stmt]

    def _recurse_sub_bodies(self, stmt: TpyStmt) -> None:
        if not is_dataclass(stmt) or not hasattr(stmt, "sub_bodies"):
            return
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if isinstance(v, list) and v and isinstance(v[0], TpyStmt):
                setattr(stmt, f.name, self.body(v))
        # TpyTry handlers and TpyMatch arm bodies hold statement lists outside
        # the dataclass's own fields, so the loop above misses them.
        handlers = getattr(stmt, "handlers", None)
        if isinstance(handlers, list):
            for h in handlers:
                if getattr(h, "body", None):
                    h.body = self.body(h.body)
        if isinstance(stmt, TpyMatch):
            for case in stmt.cases:
                # A guard runs only when its pattern matched and earlier
                # guards failed; a faithful rewrite would decompose the
                # whole match, so reject with a workaround instead of
                # letting the codegen lifter emit invalid C++.
                if _has_await(case.guard):
                    raise ParseError(
                        "'await' in a match-case guard is not supported; "
                        "assign the awaited value to a local before the "
                        "match", loc=case.guard.loc)
                if case.body:
                    case.body = self.body(case.body)

    def _lower_stmt_exprs(self, stmt: TpyStmt, prefix: list[TpyStmt]) -> None:
        """Lower each top-level expression field of `stmt` in place, appending
        any prelude statements to `prefix`."""
        if not is_dataclass(stmt):
            return
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if isinstance(v, TpyExpr):
                p, new = self._lower(v)
                prefix.extend(p)
                setattr(stmt, f.name, new)
            elif isinstance(v, list) and v and isinstance(v[0], TpyExpr):
                new_list = []
                for e in v:
                    p, ne = self._lower(e)
                    prefix.extend(p)
                    new_list.append(ne)
                setattr(stmt, f.name, new_list)
        # with-item context expressions live inside TpyWithItem objects, not a
        # plain expr field; each is evaluated once before the with, so its
        # prelude belongs before the statement like any other.
        if isinstance(stmt, TpyWith):
            for item in stmt.items:
                p, new = self._lower(item.context_expr)
                prefix.extend(p)
                item.context_expr = new

    def _assert(self, stmt: TpyAssert) -> list[TpyStmt]:
        """`assert C, M` with an awaiting M lowers to `if not C: <M's
        prelude>; assert False, M_val` -- the passing path never runs the
        message's suspension (CPython evaluates the message only on
        failure). The condition stays a once-evaluated prelude position."""
        cond_prefix, cond_val = self._lower(stmt.condition)
        msg_prefix, msg_val = self._lower(stmt.message)
        fail = TpyAssert(condition=TpyBoolLiteral(False, loc=stmt.loc),
                         message=msg_val, loc=stmt.loc)
        guard = TpyIf(condition=TpyUnaryOp("!", cond_val, loc=stmt.loc),
                      then_body=[*msg_prefix, fail],
                      else_body=[], loc=stmt.loc)
        return [*cond_prefix, guard]

    def _while(self, stmt: TpyWhile) -> list[TpyStmt]:
        # Recurse into the body / else first.
        body = self.body(stmt.body)
        orelse = self.body(stmt.orelse) if stmt.orelse else []
        if not _has_await(stmt.condition):
            stmt.body = body
            stmt.orelse = orelse
            return [self._wrap_single(stmt)]
        loc = stmt.loc
        cond_prefix, cond_val = self._lower(stmt.condition)
        # while C: BODY else: ELSE  ==>
        #   while True:
        #       <cond_prefix>
        #       if cond_val: BODY else: ELSE; break
        # body in the then-branch keeps `continue` re-evaluating the condition
        # and `break` exiting the loop, with no negation needed.
        else_branch = list(orelse) + [TpyBreak(loc=loc)]
        gate = TpyIf(condition=cond_val, then_body=body,
                     else_body=else_branch, loc=loc)
        new_body = list(cond_prefix) + [gate]
        return [TpyWhile(condition=TpyBoolLiteral(True, loc=loc),
                         body=new_body, orelse=[], loc=loc)]

    def _wrap_single(self, stmt: TpyStmt) -> TpyStmt:
        # A while whose condition itself contains a conditional construct but
        # no await (e.g. `while a or b:`) needs no restructure.
        return stmt

    # -- expression level -----------------------------------------------------

    def _lower(self, expr: TpyExpr | None) -> tuple[list[TpyStmt], TpyExpr | None]:
        """Return (prefix_stmts, value) where evaluating `prefix_stmts` then
        `value` reproduces `expr` with Python evaluation order, lifting
        conditional-position suspensions into the prefix's control flow."""
        if expr is None or not _has_await(expr):
            return [], expr
        if isinstance(expr, (TpyListComprehension, TpyDictComprehension,
                             TpySetComprehension, TpyGeneratorExpression)):
            raise ParseError(
                "`await` inside a comprehension or generator expression is "
                "not yet supported; bind the awaited values to a list first "
                "(e.g. `tmp = [await f(x) for x in xs]` is not supported -- "
                "use an explicit loop with `result.append(await f(x))`).",
                None, loc=expr.loc)
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||") \
                and _has_await(expr.right):
            return self._lower_boolop(expr)
        if isinstance(expr, TpyIfExpr) \
                and (_has_await(expr.then_expr) or _has_await(expr.else_expr)):
            return self._lower_ternary(expr)
        if isinstance(expr, TpyChainedCompare) \
                and any(_has_await(c) for c in expr.comparators):
            return self._lower_chained(expr)
        # Non-conditional node that merely *contains* an await somewhere:
        # rewrite its children, leaving the (unconditional) await for the
        # codegen lifter.
        return self._rebuild_children(expr)

    def _rebuild_children(
            self, expr: TpyExpr) -> tuple[list[TpyStmt], TpyExpr]:
        if not is_dataclass(expr):
            return [], expr
        prefix: list[TpyStmt] = []
        for f in fields(expr):
            v = getattr(expr, f.name, None)
            if isinstance(v, TpyExpr):
                p, new = self._lower(v)
                prefix.extend(p)
                setattr(expr, f.name, new)
            elif isinstance(v, list) and v and isinstance(v[0], TpyExpr):
                new_list = []
                for e in v:
                    p, ne = self._lower(e)
                    prefix.extend(p)
                    new_list.append(ne)
                setattr(expr, f.name, new_list)
        return prefix, expr

    def _decl(self, name: str, init: TpyExpr,
              loc: 'SourceLocation | None') -> TpyStmt:
        # A temp's first binding must be a TpyVarDecl: the parser lowers
        # `x = expr` for a new local to TpyVarDecl, and sema only treats
        # TpyVarDecl as introducing a binding (a bare TpyAssign to an unknown
        # name reads as "undefined variable").
        return TpyVarDecl(name=name, type=None, init=init, loc=loc)

    def _lower_boolop(
            self, expr: TpyBinOp) -> tuple[list[TpyStmt], TpyExpr]:
        # TPy `and`/`or` are value-returning (`a or b` is `a` if `a` is truthy
        # else `b`, like CPython and sync TPy's `a ? a : b`) -- NOT bool. The
        # left operand is bound to a temp (evaluated exactly once and reused as
        # both the truth test and a possible result); the right operand is only
        # evaluated on the branch the short-circuit takes. The result temp gets
        # the value, so its type is the join of both operands (a divergent-type
        # pair is rejected by sema, matching sync `a or b`).
        loc = expr.loc
        left_name = self._fresh()
        result = self._fresh()
        left_prefix, left_val = self._lower(expr.left)
        right_prefix, right_val = self._lower(expr.right)
        prefix = list(left_prefix) + [self._decl(left_name, left_val, loc)]
        use_left = [self._decl(result, TpyName(left_name, loc=loc), loc)]
        use_right = list(right_prefix) + [self._decl(result, right_val, loc)]
        if expr.op == "||":
            # or: left truthy -> result is left; else result is right.
            gate = TpyIf(condition=TpyName(left_name, loc=loc),
                         then_body=use_left, else_body=use_right, loc=loc)
        else:
            # and: left truthy -> result is right; else result is left.
            gate = TpyIf(condition=TpyName(left_name, loc=loc),
                         then_body=use_right, else_body=use_left, loc=loc)
        return prefix + [gate], TpyName(result, loc=loc)

    def _lower_ternary(
            self, expr: TpyIfExpr) -> tuple[list[TpyStmt], TpyExpr]:
        # then_expr if condition else else_expr -- value semantics. The two
        # branches are conditionally evaluated; the result temp takes the
        # branch value (sema rejects divergent branch types -- see the
        # synthetic-temp note).
        loc = expr.loc
        name = self._fresh("await_ternary")
        cond_prefix, cond_val = self._lower(expr.condition)
        then_prefix, then_val = self._lower(expr.then_expr)
        else_prefix, else_val = self._lower(expr.else_expr)
        # Both branches are the temp's first (mutually exclusive) binding --
        # TpyVarDecl in each, mirroring `if c: t = a` / `else: t = b`.
        then_branch = list(then_prefix) + [self._decl(name, then_val, loc)]
        else_branch = list(else_prefix) + [self._decl(name, else_val, loc)]
        gate = TpyIf(condition=cond_val, then_body=then_branch,
                     else_body=else_branch, loc=loc)
        return list(cond_prefix) + [gate], TpyName(name, loc=loc)

    def _lower_chained(
            self, expr: TpyChainedCompare) -> tuple[list[TpyStmt], TpyExpr]:
        # a op0 b op1 c ...  ==  (a op0 b) and (b op1 c) and ...  with each
        # operand evaluated exactly once and short-circuit on the first false.
        loc = expr.loc
        operands = [expr.left] + expr.comparators
        result = self._fresh()
        prefix: list[TpyStmt] = []
        # Bind the two operands of the first comparison (always evaluated).
        op_names: list[str] = []
        for operand in operands[:2]:
            p, v = self._lower(operand)
            prefix.extend(p)
            n = self._fresh()
            prefix.append(self._decl(n, v, loc))
            op_names.append(n)
        first_cmp = TpyBinOp(TpyName(op_names[0], loc=loc), expr.ops[0],
                             TpyName(op_names[1], loc=loc), loc=loc)
        prefix.append(self._decl(result, first_cmp, loc))
        # Each subsequent comparison is gated on the running result; its new
        # operand is evaluated (once) only when reached.
        innermost = prefix
        for i in range(1, len(expr.ops)):
            inner: list[TpyStmt] = []
            p, v = self._lower(operands[i + 1])
            inner.extend(p)
            n = self._fresh()
            inner.append(self._decl(n, v, loc))
            cmp = TpyBinOp(TpyName(op_names[i], loc=loc), expr.ops[i],
                           TpyName(n, loc=loc), loc=loc)
            inner.append(TpyAssign(TpyName(result, loc=loc), cmp, loc=loc))
            op_names.append(n)
            gate = TpyIf(condition=TpyName(result, loc=loc),
                         then_body=inner, else_body=[], loc=loc)
            innermost.append(gate)
            innermost = inner
        return prefix, TpyName(result, loc=loc)
