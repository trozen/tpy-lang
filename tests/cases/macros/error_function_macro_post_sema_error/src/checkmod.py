# tpy: macro_module
"""Error case: a deferred (post-sema) function macro raises ctx.error.

require_int_return defers a callback to after type inference; the callback
reads the return value's inferred type via ctx.type_of and errors when it
isn't Int32 -- exercising diagnostic propagation out of the post-sema phase.
"""
from tpyc.macro_api import function_macro, FunctionMacroContext, TpyReturn


@function_macro
def require_int_return(ctx: FunctionMacroContext) -> None:
    ctx.defer_until_sema_complete(_check)


def _check(ctx) -> None:
    for stmt in ctx.body:
        if isinstance(stmt, TpyReturn) and stmt.value is not None:
            t = ctx.type_of(stmt.value)
            if t is None or not t.is_int32:
                ctx.error("require_int_return: expected an Int32 return value")
