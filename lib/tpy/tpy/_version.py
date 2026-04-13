# tpy: macro_module
"""Compile-time macros exposing the tpyc compiler version to user code.

Consumed by tpy/version.py to initialise tpy.version.__version__ and
tpy.version.version_info. Each macro runs during compilation (via the
macro loader running on CPython) and emits a literal expression that
the compiler splices into the generated C++ as a constant.
"""

from tpyc.macro_api import (
    __version__ as _v, VERSION_INFO as _vi,
    CallMacroContext, Expr, call_macro, ast,
)


@call_macro
def version(ctx: CallMacroContext) -> Expr:
    return ast.str_lit(_v)


@call_macro
def version_info(ctx: CallMacroContext) -> Expr:
    return ast.tuple_lit([
        ast.int_lit(_vi[0]),
        ast.int_lit(_vi[1]),
        ast.int_lit(_vi[2]),
        ast.str_lit(_vi[3]),
        ast.int_lit(_vi[4]),
    ])
