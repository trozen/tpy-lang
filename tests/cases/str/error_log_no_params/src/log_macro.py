# tpy: macro_module
"""Call macros for f-string decomposition with deferred-copy semantics.

Per-type wrapping:
- Static strings (literals, ternary of literals) -> StaticStr (pointer only)
- Dynamic strings (variables, expressions) -> DeferredStr (string_view, copy deferred)
- Other types (int, bool, float) -> passed through unchanged

Two macros:
- log_debug(logger, f"...") -- explicit logger argument
- log(f"...") -- auto-discovers _logger field or get_logger() method on first param
"""
from tpyc.macro_api import (
    CallMacroContext, MacroArg,
    call_macro, macro_deps, ast, Expr,
)

macro_deps("log_infra")

LOGGER_QNAME = "log_infra.LogHandle"


def _wrap_parts(parts: list) -> list[Expr]:
    """Wrap f-string parts with deferred/static string wrappers."""
    infra = ast.name("log_infra")
    wrapped: list[Expr] = []
    for part in parts:
        if part.is_static_str:
            wrapped.append(ast.method_call(infra, "static_str", [part.expr]))
        elif part.type.is_str:
            wrapped.append(ast.method_call(infra, "defer_str", [part.expr]))
        else:
            wrapped.append(part.expr)
    return wrapped


def _emit_dispatch(logger_expr: Expr, fstr_arg: MacroArg, ctx: CallMacroContext) -> Expr:
    """Build log_infra.log_dispatch(logger, fmt, (args...)) from an f-string arg."""
    result = fstr_arg.as_fstring()
    if result is None:
        ctx.error("expected an f-string argument")
    fmt, parts = result
    wrapped = _wrap_parts(parts)
    infra = ast.name("log_infra")
    args_tuple = ast.tuple_lit(wrapped) if wrapped else ast.tuple_lit([])
    return ast.method_call(infra, "log_dispatch",
                           [logger_expr, ast.str_lit(fmt), args_tuple])


def _find_logger(ctx: CallMacroContext, obj: Expr, type_info) -> Expr | None:
    """Find _logger field or get_logger() method returning LogHandle on a type."""
    ft = ctx.get_field_type(type_info, "_logger")
    if ft is not None and ctx.qualified_name(ft) == LOGGER_QNAME:
        return ast.field_access(obj, "_logger")
    rt = ctx.get_method_return_type(type_info, "get_logger")
    if rt is not None and ctx.qualified_name(rt) == LOGGER_QNAME:
        return ast.method_call(obj, "get_logger")
    return None


@call_macro
def log_debug(ctx: CallMacroContext, logger_arg: MacroArg, fstr_arg: MacroArg) -> Expr:
    """Decompose f-string and emit log_infra.log_dispatch(logger, fmt, (args...))."""
    return _emit_dispatch(logger_arg.expr, fstr_arg, ctx)


@call_macro
def log(ctx: CallMacroContext, fstr_arg: MacroArg) -> Expr:
    """Auto-discovering log macro. Takes a single f-string argument.

    Inspects first parameter (self for methods, first arg for free functions)
    for a _logger field or get_logger() method returning LogHandle.
    """
    param = ctx.first_param
    if param is None:
        ctx.error("log: no parameters to inspect for logger")
    param_name, param_type = param
    logger_expr = _find_logger(ctx, ast.name(param_name), param_type)
    if logger_expr is None:
        ctx.error(f"log: no _logger field or get_logger() returning "
                  f"{LOGGER_QNAME} found on '{param_name}'")
    return _emit_dispatch(logger_expr, fstr_arg, ctx)
