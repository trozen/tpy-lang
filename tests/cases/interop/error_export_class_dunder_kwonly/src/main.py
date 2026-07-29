# A keyword-only parameter on a dunder crossing the CPython boundary is
# rejected, even though keyword-only params ARE supported on free functions,
# methods and __init__ (tests/interop/defaults). A dunder reaches CPython as a
# TYPE SLOT: the protocol hands the wrapper its operand directly, with no
# argument tuple to parse, so there is no keyword to match.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __eq__(self, *, other: "C") -> bool:  # tpyc: error(/'__eq__': keyword-only parameters/)
        return True
