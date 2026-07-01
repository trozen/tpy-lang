# __getitem__ crossing the CPython boundary must take exactly one parameter
# beyond self -- Py_mp_subscript's binaryfunc only ever hands the wrapper
# (self, key).
# tpy: ext_module
from tpy import Int32, Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __getitem__(self, i: Int32, extra: Int32) -> Int64:  # tpyc: error(/'__getitem__' crossing the CPython boundary must take exactly one parameter/)
        return self.a
