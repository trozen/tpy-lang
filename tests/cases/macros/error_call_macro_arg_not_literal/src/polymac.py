"""Call macro that requires a string-literal argument."""
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg, ast, Expr, TpyStrLiteral


@call_macro
def poly(ctx: CallMacroContext, arg: MacroArg) -> Expr:
    if not isinstance(arg.expr, TpyStrLiteral):
        ctx.error("poly() expects a string literal argument", loc=arg.expr.loc)
    return ast.str_lit(arg.expr.value)
