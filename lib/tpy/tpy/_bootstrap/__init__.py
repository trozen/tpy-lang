# tpy: native_module(forward=True)
from ._extern import (
    builtin_decorator, builtin_type,
    native, native_c, extern_c, cpp_template,
    value_ptr_coercion, native_preserves_refs,
)
from ._decorators import (
    readonly, noalloc, nocopy, pure, dynamic, error_return,
    Own, Fn,
)
