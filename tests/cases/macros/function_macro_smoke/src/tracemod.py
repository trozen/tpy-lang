"""Function-macro smoke test: read-only introspection + a warning.

Proves a @function_macro runs at compile time on the decorated function
and can read its params, return type, and body.
"""
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import function_macro, FunctionMacroContext

# A macro module runs under CPython and may read its own settings.
assert __tpy__["mod"] == "macro"


@function_macro
def trace(ctx: FunctionMacroContext) -> None:
    names = ", ".join(name for name, _ in ctx.params)
    rt = ctx.return_type.name if ctx.return_type is not None else "None"
    ctx.warning(
        f"function macro saw {ctx.function_name}({names}) -> {rt} "
        f"with {len(ctx.body)} statement(s)")
