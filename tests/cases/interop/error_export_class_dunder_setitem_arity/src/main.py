# __setitem__ crossing the CPython boundary must take exactly two parameters
# beyond self -- Py_mp_ass_subscript's objobjargproc only ever hands the
# wrapper (self, key, value).
# tpy: ext_module
from tpy import Int32, Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __setitem__(self, i: Int32, value: Int64, extra: Int32) -> None:  # tpyc: error(/'__setitem__' crossing the CPython boundary must take exactly two parameters/)
        self.a = value
