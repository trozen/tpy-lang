"""Macros whose bodies raise a class named by a dotted path, with and
without constructor arguments."""
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import ast, function_macro


@function_macro
def raise_e(ctx):
    ctx.body[:] = [ast.raise_("m.E", [ast.int_lit(7)])]


@function_macro
def raise_d(ctx):
    ctx.body[:] = [ast.raise_("m.D")]
