# tpy: macro_module
"""Post-sema deferred macro that calls note_param_mutated with an out-of-range
param index -- the context must reject it, not corrupt the host's facts."""
from tpyc.macro_api import function_macro, FunctionMacroContext


@function_macro
def bad_note(ctx: FunctionMacroContext) -> None:
    ctx.defer_until_sema_complete(_late)


def _late(ctx) -> None:
    ctx.note_param_mutated(5)
