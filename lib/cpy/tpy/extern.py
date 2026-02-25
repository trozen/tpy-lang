"""
TurboPython external linkage declarations (tpy.extern module).

CPython stubs -- these are no-ops since native globals don't exist in CPython.
"""


class _ExternLinkage:
    """Decorator for native/native_c/extern_c linkage. No-op in CPython."""
    def __call__(self, name_or_func):
        if callable(name_or_func):
            return name_or_func
        def decorator(func):
            return func
        return decorator

native = _ExternLinkage()
native_c = _ExternLinkage()
extern_c = _ExternLinkage()


def native_c_global(name: str = ""):
    """Declare a native C global variable. No-op in CPython."""
    return None


def native_global(name: str = ""):
    """Declare a native C++ global variable. No-op in CPython."""
    return None


def native_c_global_array(name: str = ""):
    """Declare a native C global array. No-op in CPython."""
    return None
