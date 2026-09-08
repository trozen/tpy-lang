"""AST -> THIR lowering, split by layer (one-directional imports):

    predicates < context < checks < expressions < statements < functions
    < resumable < simple_gen

`predicates` holds the shared type/shape facts, `context` the per-function
lowering state, `checks` the per-arm checks and classifiers, and `expressions`
the `_lower_expr` arms, `statements` statement lowering arms,
`functions` the entry points, `resumable` the async-body leaf lowering
consumed by the gen_async skeleton seam, and `simple_gen` the
simple-generator leaf lowering consumed by the gen_generators peephole seam.
The public surface below is what the codegen seam, dump, and units import --
it is the old single-file `lower.py` API, unchanged.
"""

from .context import _LowerCtx, _LowerScope, _Prescan
from .checks import _is_len_native
from .functions import (
    iter_module_callables,
    unemitted_overload_clones,
    iter_module_constructors,
    lower_constructor,
    lower_function,
    lower_top_level,
    module_native_globals,
)
from .resumable import lower_resumable
from .simple_gen import lower_simple_generator
from .statements import _persistent_alias_name

__all__ = [
    "iter_module_callables",
    "unemitted_overload_clones",
    "iter_module_constructors",
    "lower_constructor",
    "lower_function",
    "lower_top_level",
    "lower_resumable",
    "lower_simple_generator",
    "module_native_globals",
]
