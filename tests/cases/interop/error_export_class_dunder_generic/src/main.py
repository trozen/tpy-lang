# A generic dunder crossing the CPython boundary is rejected: the compiled
# method is a C++ template, but the slot wrapper needs one concrete method to
# call (no way to supply template args through a fixed CPython C-API slot).
# tpy: ext_module
from tpy import Int32, Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __len__[T](self) -> Int32:  # tpyc: error(/'__len__' cannot be generic/)
        return 0
