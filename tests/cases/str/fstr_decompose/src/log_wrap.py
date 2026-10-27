"""Wrapper call macros over log_macro.log_debug: a function that forwards an
f-string to a macro is itself a macro, so the f-string arrives decomposed."""
__tpy__ = {"mod": "macro"}
from tpyc.macro_api import CallMacroContext, MacroArg, call_macro, macro_deps, ast, Expr
import log_macro

# The expansion names log_infra (through log_macro), so this module must
# make it available to its callers itself.
macro_deps("log_macro", "log_infra")


@call_macro
def log_free(ctx: CallMacroContext, logger: MacroArg, fs: MacroArg) -> Expr:
    """Composes in Python: the expansion is log_debug's own."""
    return log_macro.log_debug(ctx, logger, fs)


@call_macro
def log_free_chain(ctx: CallMacroContext, logger: MacroArg, fs: MacroArg) -> Expr:
    """Expands to a call of the log_debug macro, expanded in turn."""
    return ast.call("log_debug", [logger.expr, fs.expr])
