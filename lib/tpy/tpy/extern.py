# tpy: native_module(forward=True)
from ._bootstrap._extern import (
    builtin_decorator, builtin_type,
    native, native_c, extern_c, export, cpp_template,
    value_ptr_coercion, native_preserves_refs,
    native_c_global, native_global, native_c_global_array,
    type_param_default, DefaultInt,
)
