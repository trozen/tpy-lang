# An exposed type crosses as a TOP-LEVEL container element, but nested inside a
# container element it is deferred: the per-element C++ type render doesn't yet
# namespace-qualify an exposed leaf below the top level (dict[str, list[Counter]]
# would emit a bare 'Counter' that the glue namespace can't resolve).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: Int64):
        self.value = v


@export
def f(m: dict[str, list[Counter]]) -> Int64:  # tpyc: error(/'Counter' nested inside a container element/)
    return len(m)
