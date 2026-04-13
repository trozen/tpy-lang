# tpy: native_module
from ._extern import (
    builtin_decorator, builtin_type,
    native, cpp_template,
    value_ptr_coercion, native_preserves_refs,
)
from ._decorators import (
    readonly, noalloc, nocopy, pure, inline, dynamic, error_return,
    Own, Fn,
)
