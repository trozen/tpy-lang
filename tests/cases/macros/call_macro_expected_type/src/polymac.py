# tpy: macro_module
"""Call macro that picks a literal kind from ctx.expected_type."""
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg, ast, Expr, TpyStrLiteral


@call_macro
def poly(ctx: CallMacroContext, arg: MacroArg) -> Expr:
    # poly reads the literal text at compile time, so the arg must be a string
    # literal -- a str-typed variable would pass arg.type.is_str but has no value.
    if not isinstance(arg.expr, TpyStrLiteral):
        ctx.error("poly() expects a string literal argument", loc=arg.expr.loc)
    text = arg.expr.value
    t = ctx.expected_type
    # An Optional slot still selects by its inner type -- the literal we emit
    # (a bare bool/int) is assignable into the Optional.
    if t is not None and t.is_optional:
        t = t.unwrap_optional()
    if t is not None and t.is_bool and text in ("true", "false"):
        return ast.bool_lit(text == "true")
    if t is not None and t.is_int:
        return ast.int_lit(int(text))
    return ast.str_lit(text)
