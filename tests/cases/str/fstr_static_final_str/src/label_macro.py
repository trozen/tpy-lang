# tpy: macro_module
"""Macro that exposes MacroFStringPart.is_static_str to observable output.

Each f-string is lowered to ``label_fmt(fmt, ("static|dynamic", value), ...)``
so the test output reveals how is_static_str classifies each part.
"""
from tpyc.macro_api import (
    CallMacroContext, MacroArg,
    call_macro, macro_deps, ast, Expr,
)

macro_deps("label_infra")


@call_macro
def label(ctx: CallMacroContext, fstr_arg: MacroArg) -> Expr:
    result = fstr_arg.as_fstring()
    if result is None:
        ctx.error("expected an f-string argument")
    fmt, parts = result
    infra = ast.name("label_infra")
    tagged: list[Expr] = []
    for part in parts:
        tag = "static" if part.is_static_str else "dynamic"
        tagged.append(ast.method_call(infra, "tag", [ast.str_lit(tag), part.expr]))
    return ast.method_call(infra, "emit", [ast.str_lit(fmt), ast.list_lit(tagged)])
