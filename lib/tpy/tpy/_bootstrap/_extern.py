# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Extern decorator stubs. builtin_decorator is the bootstrap primitive
# (auto-resolved within this module via _EXTERN_KEYWORDS). All other
# decorators are defined using @builtin_decorator.

@builtin_decorator("tpy.extern.builtin_decorator")
def builtin_decorator(key: str): ...

@builtin_decorator("tpy.extern.builtin_type")
def builtin_type(key: str): ...

@builtin_decorator("tpy.extern.builtin_function")
def builtin_function(key: str): ...

@builtin_decorator("tpy.extern.native")
def native(name: str = "", function: bool = False): ...

@builtin_decorator("tpy.extern.native_c")
def native_c(name: str = ""): ...

@builtin_decorator("tpy.extern.extern_c")
def extern_c(name: str = ""): ...

# Prefer @native over @cpp_template -- use only when @native can't express the
# call (e.g. wrapping in a constructor, type cast, or non-trivial expression).
@builtin_decorator("tpy.extern.cpp_template")
def cpp_template(template: str): ...

@builtin_decorator("tpy.extern.value_ptr_coercion")
def value_ptr_coercion(): ...

@builtin_decorator("tpy.extern.native_preserves_refs")
def native_preserves_refs(): ...

# Workaround for Python 3.12 -- Python 3.13+ has native type param defaults
# (PEP 696): def round[T = int](x: float) -> T: ...
@builtin_decorator("tpy.extern.type_param_default")
def type_param_default(): ...

# Sentinel for @type_param_default: resolves to the configured --default-int type.
# TODO: make this a real type alias (e.g. `type DefaultInt = ...`) with compiler
# support, so it can be used in type annotations beyond @type_param_default.
@builtin_type("tpy.extern.DefaultInt")
class DefaultInt: pass


# Native global variable declarations for C/C++ interop.
# TODO: design a macro function mechanism so these set variable properties
# (linkage, extern name) through the macro system instead of special handling.
@builtin_function("tpy.extern.native_c_global")
def native_c_global(name: str = ""): ...

@builtin_function("tpy.extern.native_global")
def native_global(name: str = ""): ...

@builtin_function("tpy.extern.native_c_global_array")
def native_c_global_array(name: str = ""): ...
