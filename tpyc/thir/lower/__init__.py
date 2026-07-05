"""AST -> THIR lowering, split by layer (one-directional imports):

    predicates < context < expressions < statements < functions

`predicates` holds the shared type/shape facts, `context` the per-function
lowering state, `expressions`/`statements` the gate + lowering arms for
their construct families (gate and lowering co-evolve per cell, so they
live together per construct), and `functions` the entry points. The public
surface below is what the codegen seam, dump, and units import -- it is
the old single-file `lower.py` API, unchanged.
"""

from .context import _LowerCtx, _Prescan, _WalkState
from .expressions import _is_len_native
from .functions import (
    iter_module_callables,
    iter_module_constructors,
    lower_constructor,
    lower_function,
    lower_module,
    module_native_globals,
)
from .statements import _persistent_alias_name, _stmt_eligible

__all__ = [
    "iter_module_callables",
    "iter_module_constructors",
    "lower_constructor",
    "lower_function",
    "lower_module",
    "module_native_globals",
]
