# __repr__ crossing the CPython boundary must return str -- Py_tp_repr's
# reprfunc wrapper marshals via to_py(std::string), so a non-str return has
# nothing to convert.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    def __repr__(self) -> Int64:  # tpyc: error(/'__repr__' crossing the CPython boundary must return str/)
        return self.a
