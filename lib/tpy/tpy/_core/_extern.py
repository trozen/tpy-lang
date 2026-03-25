# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Extern decorator stubs. builtin_decorator is the bootstrap primitive
# (auto-resolved within this module via _EXTERN_KEYWORDS). All other
# decorators are defined using @builtin_decorator.

@builtin_decorator("tpy.extern.builtin_decorator")
def builtin_decorator(key: str): ...

@builtin_decorator("tpy.extern.builtin_type")
def builtin_type(key: str): ...

@builtin_decorator("tpy.extern.native")
def native(name: str = "", function: bool = False): ...

@builtin_decorator("tpy.extern.native_c")
def native_c(name: str = ""): ...

@builtin_decorator("tpy.extern.extern_c")
def extern_c(name: str = ""): ...

@builtin_decorator("tpy.extern.cpp_template")
def cpp_template(template: str): ...

@builtin_decorator("tpy.extern.value_ptr_coercion")
def value_ptr_coercion(): ...

@builtin_decorator("tpy.extern.native_preserves_refs")
def native_preserves_refs(): ...
