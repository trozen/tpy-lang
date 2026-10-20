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
