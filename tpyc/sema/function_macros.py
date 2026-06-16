"""Function-macro application phase.

Runs at pass 5.5 alongside builder-trace expansion: after function
signatures are registered (params / return types resolved) and before
function bodies are type-checked, so a macro's body mutations are seen by
sema. Resolves `@function_macro` decorators and invokes them with a
`FunctionMacroContext`.

Lives on the sema side (like `macros.py`) because the macro API exposes
sema state -- resolved param/return types, registry lookups -- that the
resolver needs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..macro_api import (
    FunctionMacroContext, PostSemaFunctionMacroContext, MacroError,
)
from ..diagnostics import SemanticError

if TYPE_CHECKING:
    from ..parse.nodes import TpyFunction
    from .context import SemanticContext


def run_function_macros(
    func: 'TpyFunction', ctx: 'SemanticContext', module_qname: str,
    module_data: 'Any' = None,
) -> None:
    """Resolve and apply `@function_macro` decorators on `func` in place.

    `module_data` is the enclosing module's opaque plugin payload
    (`TpyModule.macro_data`), surfaced to each macro as `ctx.module_data`.

    No-op when the function carries no pending macros.
    """
    if not func.pending_macros:
        return
    registry = ctx.macro_registry
    if registry is None:
        raise SemanticError(
            f"function '{func.name}' uses a macro decorator but no macro "
            f"registry is available", func.loc)
    # Reverse so the innermost decorator runs first (outer(inner(f))).
    for qname, kwargs in reversed(func.pending_macros):
        parts = qname.rsplit(".", 1)
        if len(parts) != 2:
            raise SemanticError(f"Invalid macro name '{qname}'", func.loc)
        mod_name, fn_name = parts
        macro_fn = registry.get_function_macro(mod_name, fn_name)
        if macro_fn is None:
            raise SemanticError(f"Unknown function macro '{qname}'", func.loc)
        fmctx = FunctionMacroContext(ctx, func, module_qname, loc=func.loc,
                                     module_data=module_data)
        try:
            macro_fn(fmctx, **kwargs)
        except MacroError as e:
            raise SemanticError(str(e), e.loc or func.loc) from e
        except SemanticError:
            raise
        except Exception as e:
            raise SemanticError(
                f"{qname}(): function macro raised {type(e).__name__}: {e}",
                func.loc) from e


def run_deferred_sema_macros(
    func: 'TpyFunction', ctx: 'SemanticContext', module_qname: str,
    module_data: 'Any' = None,
) -> None:
    """Drain callbacks registered via ctx.defer_until_sema_complete on `func`,
    AFTER its body has been type-checked (post pass 7).

    Each callback gets a PostSemaFunctionMacroContext with inferred-type
    access. Drains the list before iterating so a callback that re-defers is
    a hard error (mirrors the class-macro deferred runner) rather than a
    silent loop. No-op when nothing was deferred.
    """
    callbacks = func.pending_deferred_sema_macros
    if not callbacks:
        return
    func.pending_deferred_sema_macros = []
    for callback in callbacks:
        pmctx = PostSemaFunctionMacroContext(
            ctx, func, module_qname, module_data=module_data)
        try:
            callback(pmctx)
        except MacroError as e:
            raise SemanticError(str(e), e.loc or func.loc) from e
        except SemanticError:
            raise
        except Exception as e:
            raise SemanticError(
                f"deferred sema macro raised {type(e).__name__}: {e}",
                func.loc) from e
        if func.pending_deferred_sema_macros:
            raise SemanticError(
                "deferred sema macro registered another deferred callback "
                "(not supported)", func.loc)
