# tpy: macro_module
"""Error case: a deferred post-sema callback that re-defers is rejected.

bad_redefer defers _first; _first (running post-sema) calls
defer_until_sema_complete again. The drain runs once and rejects the second
registration rather than looping.
"""
from tpyc.macro_api import function_macro, FunctionMacroContext


@function_macro
def bad_redefer(ctx: FunctionMacroContext) -> None:
    ctx.defer_until_sema_complete(_first)


def _first(ctx) -> None:
    ctx.defer_until_sema_complete(_second)


def _second(ctx) -> None:
    ctx.error("unreachable: the re-defer should be rejected first")
