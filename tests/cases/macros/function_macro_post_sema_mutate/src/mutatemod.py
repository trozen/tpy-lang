# tpy: macro_module
"""Post-sema macro: rewrites `sentinel(c)` -> `c.bump()` and calls
note_param_mutated so the param `c` is emitted mutable (not const&)."""
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
def resolve_bump(ctx: FunctionMacroContext) -> None:
    if _sentinel_calls(ctx.body):
        ctx.defer_until_sema_complete(_resolve)


def _resolve(ctx) -> None:
    int32 = ctx.resolve_type("int32")
    param_names = [n for n, _ in ctx.params]
    for call in _sentinel_calls(ctx.body):
        recv = call.args[0]
        bumped = ast.method_call(recv, "bump", [])
        ctx.set_expr_type(bumped, int32)
        ctx.replace_expr(call, bumped)
        if isinstance(recv, TpyName) and recv.name in param_names:
            ctx.note_param_mutated(param_names.index(recv.name))
