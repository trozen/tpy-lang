"""
TurboPython external linkage declarations (tpy.extern module).

CPython stubs -- these are no-ops since native globals don't exist in CPython.
"""


def native_c_global(name: str = ""):
    """Declare a native C global variable. No-op in CPython."""
    return None


def native_global(name: str = ""):
    """Declare a native C++ global variable. No-op in CPython."""
    return None


def native_c_global_array(name: str = ""):
    """Declare a native C global array. No-op in CPython."""
    return None
