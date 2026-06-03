# tpy: macro_module
"""Function macro: type-deduce string-literal bool locals.

A local introduced as `x = "true"/"false"` is retyped as bool and its
initializer rewritten to a bool literal. Proves annotate_local +
replace_expr end-to-end.
"""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast,
    VarDecl, StrLiteral,
)


@function_macro
def bool_locals(ctx: FunctionMacroContext) -> None:
    # Reuse the function's declared bool return type as the target type; a
    # full resolver would resolve `bool` via the downstream slot.
    bool_t = ctx.return_type
    for stmt in list(ctx.body):
        if (isinstance(stmt, VarDecl) and stmt.type is None
                and isinstance(stmt.init, StrLiteral)
                and stmt.init.value in ("true", "false")):
            ctx.replace_expr(stmt.init, ast.bool_lit(stmt.init.value == "true"))
            ctx.annotate_local(stmt.name, bool_t)
