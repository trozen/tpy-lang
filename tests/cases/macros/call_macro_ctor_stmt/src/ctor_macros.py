__tpy__ = {"mod": "macro"}
# `build(v)` expands to the constructor call `Tick(v)`, named in the caller's scope.
from tpyc.macro_api import CallMacroContext, Expr, MacroArg, ast, call_macro


@call_macro
def build(ctx: CallMacroContext, v: MacroArg) -> Expr:
    return ast.call("Tick", [v.expr])
