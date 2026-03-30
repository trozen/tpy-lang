# tpy: macro_module
"""Test macro module with a concat call-site macro."""
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg, ast, Expr


def _to_str(arg: MacroArg, quote_str: bool) -> Expr:
    """Wrap arg in str() unless it's already a string type."""
    if arg.type.is_str:
        if quote_str:
            return ast.binop(
                ast.str_lit("'"),
                "+",
                ast.binop(arg.expr, "+", ast.str_lit("'")),
            )
        return arg.expr
    return ast.call("str", [arg.expr])


@call_macro
def concat(
    ctx: CallMacroContext, *args: MacroArg,
    sep: str = " ", quote_str: bool = False,
) -> Expr:
    """Concatenate string representations of arguments with separator."""
    if not args:
        return ast.str_lit("")
    sep_expr = ast.str_lit(sep)
    parts = [_to_str(a, quote_str) for a in args]
    result = parts[0]
    for p in parts[1:]:
        result = ast.binop(result, "+", ast.binop(sep_expr, "+", p))
    return result
