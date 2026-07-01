# A comparison dunder crossing the CPython boundary must compare against the
# record's own type -- the compiled Py_tp_richcompare wrapper only accepts
# `other` being an instance of this class (single type-guard-then-switch
# shape, unlike arithmetic operators which marshal each op's operand per its
# own declared type).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __eq__(self, other: Int64) -> bool:  # tpyc: error(/'__eq__' parameter 'other' must be 'C'/)
        return self.a == other
