# A dunder crossing the CPython boundary can't be a @staticmethod -- the
# richcompare/repr/hash slot wrappers all unwrap a live `self` payload.
# tpy: ext_module
from tpy import Int64, UInt64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    @staticmethod
    def __hash__() -> UInt64:  # tpyc: error(/'__hash__' cannot be a @staticmethod/)
        return UInt64(0)
