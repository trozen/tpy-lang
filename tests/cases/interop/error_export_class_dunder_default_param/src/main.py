# A default value on a dunder's parameter crossing the CPython boundary is
# rejected: CPython's own binary-op/richcompare protocol always supplies
# both operands (there's no way to "omit" the right side of `a == b`), so a
# default is unreachable through the compiled slot -- and the glue's fixed
# marshal-from-PyObject expression has no fallback value to substitute
# either, so this would otherwise fail deep in the C++ build instead.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __eq__(self, other: "C" = None) -> bool:  # tpyc: error(/'__eq__': default parameter values are not supported/)
        return True
