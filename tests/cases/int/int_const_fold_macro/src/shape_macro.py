"""Call macros over int constants: one returns its argument expression, one
reports whether that argument is still an operator node, one builds an
operator tree of literals."""
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import call_macro, ast, TpyBinOp


@call_macro
def identity(ctx, arg):
    return arg.expr


@call_macro
def is_binop(ctx, arg):
    return ast.int_lit(1 if isinstance(arg.expr, TpyBinOp) else 0)


@call_macro
def generated(ctx):
    return ast.binop(ast.binop(ast.binop(ast.int_lit(2), '**', ast.int_lit(30)),
                               '*', ast.int_lit(4)), '//', ast.int_lit(4))
