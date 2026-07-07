# An exposed enum crosses as a container element in any position, but an exposed
# CLASS cannot be a set element or dict key: a class does not conform to
# Hashable, so the general set/dict-key check rejects it before the boundary --
# exactly the deferral we want (list/tuple element and dict value still cross).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: Int64):
        self.value = v


@export
def sink(cs: set[Counter]) -> Int64:  # tpyc: error(/'Counter' cannot be used as a set element/)
    return len(cs)
