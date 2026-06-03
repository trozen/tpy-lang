# tpy: macro_module
"""Call macro that requires a string-literal argument."""
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg, ast, Expr, TpyStrLiteral


@call_macro
def poly(ctx: CallMacroContext, arg: MacroArg) -> Expr:
    if not isinstance(arg.expr, TpyStrLiteral):
        ctx.error("poly() expects a string literal argument", loc=arg.expr.loc)
    return ast.str_lit(arg.expr.value)
