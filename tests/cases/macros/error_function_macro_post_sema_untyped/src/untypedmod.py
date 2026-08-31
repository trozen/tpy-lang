# tpy: macro_module
"""Deferred (post-sema) function macro that violates its own contract.

`replace_expr` after inference means nothing re-types the replacement, so a
callback that skips `set_expr_type` leaves a node codegen has no type for.
The macro is deliberately wrong -- the case pins the diagnostic that catches
it, not the macro.
"""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast, TpyCall, TpyName,
)


def _sentinel_calls(body):
    out = []
    for stmt in body:
        for expr in stmt.exprs():
            if (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
                    and expr.func.name == "sentinel"):
                out.append(expr)
    return out


@function_macro
def untyped_rewrite(ctx: FunctionMacroContext) -> None:
    ctx.defer_until_sema_complete(_resolve)


def _resolve(ctx) -> None:
    for call in _sentinel_calls(ctx.body):
        summed = ast.binop(call.args[0], "+", call.args[1])
        ctx.replace_expr(call, summed)
