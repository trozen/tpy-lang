__tpy__ = {"mod": "macro"}
# `trimmed(s)` expands to `(" " + s).strip()`. CPython imports this module as
# a plain one, so there the call runs on the string itself (`arg` unset).
from tpyc.macro_api import CallMacroContext, Expr, MacroArg, ast, call_macro


@call_macro
def trimmed(ctx: CallMacroContext, arg: MacroArg | None = None) -> Expr:
    if arg is None:
        return (" " + ctx).strip()
    return ast.method_call(ast.binop(ast.str_lit(" "), "+", arg.expr),
                           "strip")


@call_macro
def twice(ctx: CallMacroContext, arg: MacroArg | None = None) -> Expr:
    """`twice(x)` expands to `(x, x)`, embedding the analyzed argument twice."""
    if arg is None:
        return (ctx, ctx)
    return ast.tuple_lit([arg.expr, arg.expr])


@call_macro
def clone_edit(ctx: CallMacroContext, arg: MacroArg | None = None) -> Expr:
    """`clone_edit(n)` expands to `(n, n + 1)` by editing a clone of `n`."""
    if arg is None:
        return (ctx, ctx + 1)
    edited = ast.clone(arg.expr)
    edited.value += 1
    return ast.tuple_lit([arg.expr, edited])


@call_macro
def first(ctx: CallMacroContext, arg: MacroArg | None = None) -> Expr:
    """`first(xs)` expands to `xs[0]`."""
    if arg is None:
        return ctx[0]
    return ast.subscript(arg.expr, ast.int_lit(0))


@call_macro
def identity(ctx: CallMacroContext, arg: MacroArg | None = None) -> Expr:
    """`identity(x)` expands to `x`."""
    if arg is None:
        return ctx
    return arg.expr
