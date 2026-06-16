# tpy: macro_module
"""Post-sema deferred macro that wrongly calls annotate_local.

annotate_local influences inference, which has already run by the time a
deferred callback fires -- so the post-sema context must reject it rather
than silently no-op. Pins that contract.
"""
from tpyc.macro_api import function_macro, FunctionMacroContext


@function_macro
def bad_annotate(ctx: FunctionMacroContext) -> None:
    ctx.defer_until_sema_complete(_late)


def _late(ctx) -> None:
    bool_t = ctx.resolve_type("bool")
    ctx.annotate_local("x", bool_t)
