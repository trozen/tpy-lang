# A positional-only parameter on a dunder crossing the CPython boundary is
# rejected, completing the trio with the default and keyword-only siblings.
# Positional-only params ARE supported on free functions, methods and __init__
# (tests/interop/defaults) via an empty kwlist entry -- but a dunder has no
# kwlist at all: CPython's type slot hands the wrapper its operand directly.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __eq__(self, other: "C", /) -> bool:  # tpyc: error(/'__eq__': positional-only parameters/)
        return True
