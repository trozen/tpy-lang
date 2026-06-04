# tpy: macro_module
"""Function macro that mints the bool type via ctx.resolve_type.

Unlike function_macro_bool_locals (which borrowed bool off the return type),
this resolves "bool" by name -- proving resolve_type supplies a usable type
independent of the function signature.
"""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast, VarDecl, StrLiteral,
)


@function_macro
def resolve_bool(ctx: FunctionMacroContext) -> None:
    bool_t = ctx.resolve_type("bool")
    if bool_t is None:
        ctx.error("resolve_type('bool') returned None")
    for stmt in list(ctx.body):
        if (isinstance(stmt, VarDecl) and stmt.type is None
                and isinstance(stmt.init, StrLiteral)
                and stmt.init.value in ("true", "false")):
            ctx.replace_expr(stmt.init, ast.bool_lit(stmt.init.value == "true"))
            ctx.annotate_local(stmt.name, bool_t)
