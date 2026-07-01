# __eq__ crossing the CPython boundary must take exactly one parameter beyond
# self -- Py_tp_richcompare only ever hands the wrapper (self, other, op).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __eq__(self, other: C, extra: Int64) -> bool:  # tpyc: error(/'__eq__' crossing the CPython boundary must take exactly one parameter/)
        return self.a == other.a
