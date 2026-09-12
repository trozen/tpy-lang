# __neg__ crossing the CPython boundary must return a boundary-marshallable
# type; a plain (non-@export) record has no CPython type to wrap it in.
# tpy: ext_module
from tpy import int64, Own
from tpy.extern import export


class NotExposed:
    def __init__(self, v: int64):
        self.v = v


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __neg__(self) -> Own[NotExposed]:  # tpyc: error(/'__neg__' return type cannot cross the CPython boundary/)
        return NotExposed(-self.a)
