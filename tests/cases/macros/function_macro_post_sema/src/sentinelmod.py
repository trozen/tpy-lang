# tpy: macro_module
"""Function macro exercising the post-sema deferred phase.

`resolve_sentinel` runs at pass 5.5 but only schedules work: it defers a
callback via ctx.defer_until_sema_complete. The callback runs after the body
is type-checked, reads the first argument's inferred type via ctx.type_of
(impossible at pass 5.5 -- expr_types is empty there), rewrites the
`sentinel(a, b)` call to `a + b` when the arg is int32, and types the emitted
BinOp via set_expr_type so codegen sees it.
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
def resolve_sentinel(ctx: FunctionMacroContext) -> None:
    if _sentinel_calls(ctx.body):
        ctx.defer_until_sema_complete(_resolve)


def _resolve(ctx) -> None:
    for call in _sentinel_calls(ctx.body):
        arg0 = call.args[0]
        t = ctx.type_of(arg0)
        if t is None or not t.is_int32:
            ctx.error("post-sema macro expected an int32 first argument")
        summed = ast.binop(call.args[0], "+", call.args[1])
        ctx.set_expr_type(summed, t)
        ctx.replace_expr(call, summed)
