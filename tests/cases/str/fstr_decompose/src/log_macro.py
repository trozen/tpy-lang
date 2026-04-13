# tpy: macro_module
"""Call macro that decomposes an f-string into format template + tuple of args.

Per-type wrapping with deferred-copy semantics:
- Static strings (literals, ternary of literals) -> StaticStr (pointer only)
- Dynamic strings (variables, expressions) -> DeferredStr (string_view, copy deferred)
- Other types (int, bool, float) -> passed through unchanged
"""
from tpyc.macro_api import (
    CallMacroContext, MacroArg,
    call_macro, macro_deps, ast, Expr,
)

macro_deps("log_infra")


@call_macro
def log_debug(ctx: CallMacroContext, logger_arg: MacroArg, fstr_arg: MacroArg) -> Expr:
    """Decompose f-string and emit log_infra.log_dispatch(logger, fmt, (args...))."""
    result = fstr_arg.as_fstring()
    if result is None:
        ctx.error("log_debug: second argument must be an f-string")

    fmt, parts = result

    infra = ast.name("log_infra")
    wrapped: list[Expr] = []
    for part in parts:
        if part.is_static_str:
            wrapped.append(ast.method_call(infra, "static_str", [part.expr]))
        elif part.type.is_str:
            wrapped.append(ast.method_call(infra, "defer_str", [part.expr]))
        else:
            wrapped.append(part.expr)

    args_tuple = ast.tuple_lit(wrapped) if wrapped else ast.tuple_lit([])
    return ast.method_call(infra, "log_dispatch",
                           [logger_arg.expr, ast.str_lit(fmt), args_tuple])
