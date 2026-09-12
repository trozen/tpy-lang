# An exposed enum crosses as a tuple element (a value type), but an exposed
# CLASS as a tuple element is deferred: a tuple's non-value element uses borrow
# form (const Cls*) while the per-element marshal builds storage form. Use a
# list/dict of the class, or an enum/value-type tuple element.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: int64):
        self.value = v


@export
def f(t: tuple[int64, Counter]) -> int64:  # tpyc: error(/exposed-class tuple element 'Counter'/)
    return t[0]
