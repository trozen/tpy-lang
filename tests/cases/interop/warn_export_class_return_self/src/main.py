# A borrow-form (`-> Cls`) return of an exposed class warns (the instance is
# copied across the boundary, losing identity); the Own[Cls] form must NOT warn.
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export


@export
class Box:
    def __init__(self, v: Int64):
        self.v = v

    def me(self) -> "Box":
        return self  # tpyc: warning(/copied across the CPython boundary/)


@export
def identity(b: Box) -> Box:
    return b  # tpyc: warning(/copied across the CPython boundary/)


@export
def fresh(v: Int64) -> Own[Box]:
    return Box(v)  # tpyc: ok
