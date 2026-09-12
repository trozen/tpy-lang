# __setitem__ crossing the CPython boundary must take exactly two parameters
# beyond self -- Py_mp_ass_subscript's objobjargproc only ever hands the
# wrapper (self, key, value).
# tpy: ext_module
from tpy import int32, int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __setitem__(self, i: int32, value: int64, extra: int32) -> None:  # tpyc: error(/'__setitem__' crossing the CPython boundary must take exactly two parameters/)
        self.a = value
