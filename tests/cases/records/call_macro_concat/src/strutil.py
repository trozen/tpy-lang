# tpy: macro_module
"""Test macro module with a concat call-site macro."""
from tpyc.macro_api import call_macro, CallMacroContext, MacroArg
from tpyc.parse import TpyExpr, TpyCall, TpyName, TpyBinOp, TpyStrLiteral


def _to_str(arg: MacroArg, quote_str: bool) -> TpyExpr:
    """Wrap arg in str() unless it's already a string type."""
    if arg.type.is_str:
        if quote_str:
            return TpyBinOp(
                left=TpyStrLiteral(value="'"),
                op="+",
                right=TpyBinOp(left=arg.expr, op="+", right=TpyStrLiteral(value="'")),
            )
        return arg.expr
    return TpyCall(func=TpyName("str"), args=[arg.expr])


@call_macro
def concat(
    ctx: CallMacroContext, *args: MacroArg,
    sep: str = " ", quote_str: bool = False,
) -> TpyExpr:
    """Concatenate string representations of arguments with separator."""
    if not args:
        return TpyStrLiteral(value="")
    sep_expr = TpyStrLiteral(value=sep)
    parts = [_to_str(a, quote_str) for a in args]
    result = parts[0]
    for p in parts[1:]:
        result = TpyBinOp(left=result, op="+", right=TpyBinOp(left=sep_expr, op="+", right=p))
    return result
