# A dunder crossing the CPython boundary can't be a @staticmethod -- the
# richcompare/repr/hash slot wrappers all unwrap a live `self` payload.
# tpy: ext_module
from tpy import int64, uint64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    @staticmethod
    def __hash__() -> uint64:  # tpyc: error(/'__hash__' cannot be a @staticmethod/)
        return uint64(0)
