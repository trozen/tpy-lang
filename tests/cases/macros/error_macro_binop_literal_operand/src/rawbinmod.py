# tpy: macro_module
"""A post-sema function macro that rewrites a marker call into `a + 1`."""
from tpyc.macro_api import (
    FunctionMacroContext, TpyCall, TpyName, ast, function_macro,
)


def _calls(body, fname):
    out = []
    for stmt in body:
        for expr in stmt.exprs():
            if (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
                    and expr.func.name == fname):
                out.append(expr)
    return out


@function_macro
def to_literal_add(ctx: FunctionMacroContext) -> None:
    if _calls(ctx.body, "sentinel"):
        ctx.defer_until_sema_complete(_resolve_literal)


def _resolve_literal(ctx) -> None:
    for call in _calls(ctx.body, "sentinel"):
        t = ctx.type_of(call.args[0])
        summed = ast.binop(call.args[0], "+", ast.int_lit(1))
        ctx.set_expr_type(summed, t)
        ctx.replace_expr(call, summed)
