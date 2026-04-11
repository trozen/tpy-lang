"""
TurboPython external linkage declarations (tpy.extern module).

CPython stubs -- these are no-ops since native globals don't exist in CPython.
"""


class _ExternLinkage:
    """Decorator for native/native_c/extern_c/export linkage. No-op in CPython."""
    def __call__(self, name_or_func=None, **kwargs):
        if callable(name_or_func):
            return name_or_func
        def decorator(func):
            return func
        if name_or_func is None and not kwargs:
            return decorator
        return decorator

native = _ExternLinkage()
native_c = _ExternLinkage()
extern_c = _ExternLinkage()
export = _ExternLinkage()


def cpp_template(template: str):
    """C++ code template. No-op in CPython."""
    def decorator(func):
        return func
    return decorator


def native_preserves_refs(func):
    """Marks a native method as not invalidating iterators/references. No-op in CPython."""
    return func


def native_c_global(name: str = ""):
    """Declare a native C global variable. No-op in CPython."""
    return None


def native_global(name: str = "", binding: str = "", array: bool = False):
    """Declare a native C/C++ global variable. No-op in CPython."""
    return None


def native_c_global_array(name: str = ""):
    """Declare a native C global array. No-op in CPython."""
    return None


def value_ptr_coercion(func):
    """Enable T -> Ptr[T] coercion at call sites. No-op in CPython."""
    return func


def builtin_type(key: str):
    """Declare a class as a builtin type with the given qualified key. No-op in CPython."""
    def decorator(cls):
        return cls
    return decorator
