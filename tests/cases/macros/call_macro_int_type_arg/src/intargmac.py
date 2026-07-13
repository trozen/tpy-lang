# tpy: macro_module
"""Call macro that reads a MacroArg's TypeInfo, including its type_args --
exercises TypeInfo.from_tpy_type over a generic whose `N: int` param binds a
plain int (the `TpyType | int` type-arg convention)."""
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg, ast, Expr


@call_macro
def describe(ctx: CallMacroContext, arg: MacroArg) -> Expr:
    t = arg.type
    parts = []
    for ta in t.type_args:
        parts.append(str(ta) if isinstance(ta, int) else ta.name)
    text = f"{len(t.type_args)} args: {', '.join(parts)}"
    return ast.str_lit(text)
