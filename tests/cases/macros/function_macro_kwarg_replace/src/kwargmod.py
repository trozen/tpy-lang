# tpy: macro_module
"""Function macro exercising replace_expr on keyword-argument expressions.

Call kwargs live in a `dict[str, TpyExpr]` field; this verifies the
replace_expr walker descends into dict-valued fields (regression guard for
the kwargs-unreachable gap).
"""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast, StrLiteral, VarDecl, TpyCall,
)


@function_macro
def kwarg_bool(ctx: FunctionMacroContext) -> None:
    # Collect string-literal bool kwargs first, then replace (avoid mutating
    # a kwargs dict mid-iteration).
    targets = []
    for stmt in ctx.body:
        if isinstance(stmt, VarDecl) and isinstance(stmt.init, TpyCall):
            for v in stmt.init.kwargs.values():
                if isinstance(v, StrLiteral) and v.value in ("true", "false"):
                    targets.append(v)
    for v in targets:
        ctx.replace_expr(v, ast.bool_lit(v.value == "true"))
