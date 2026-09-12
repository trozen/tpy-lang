# __len__ crossing the CPython boundary must return a fixed-width int --
# Py_mp_length/Py_sq_length's lenfunc returns Py_ssize_t.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __len__(self) -> int:  # tpyc: error(/'__len__' crossing the CPython boundary must return a fixed-width integer type/)
        return self.a
