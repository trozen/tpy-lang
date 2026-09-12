# __contains__ crossing the CPython boundary must return bool -- Py_sq_contains's
# objobjproc returns -1/0/1, mapped from a bool result.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __contains__(self, value: int64) -> int64:  # tpyc: error(/'__contains__' crossing the CPython boundary must return bool/)
        return self.a
