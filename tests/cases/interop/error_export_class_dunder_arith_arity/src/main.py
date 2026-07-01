# __add__ crossing the CPython boundary must take exactly one parameter
# beyond self -- Py_nb_add's binaryfunc only ever hands the wrapper (a, b).
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __add__(self, other: C, extra: Int64) -> Own[C]:  # tpyc: error(/'__add__' crossing the CPython boundary must take exactly one parameter/)
        return C(self.a + other.a + extra)
