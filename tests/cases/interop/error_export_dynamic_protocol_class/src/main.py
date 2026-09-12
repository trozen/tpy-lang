# An @export class declaring a @dynamic protocol base is rejected: the virtual
# C++ base's vptr displaces the payload layout the CPython instance embedding
# relies on (Instance<T> casts).
# tpy: ext_module
from typing import Protocol
from tpy import int64, dynamic
from tpy.extern import export


@dynamic
class Countable(Protocol):
    def count(self) -> int64: ...


@export
class Bag(Countable):  # tpyc: error(/implementing @dynamic protocol 'Countable' cannot be exposed/)
    def __init__(self, n: int64):
        self.n = n

    def count(self) -> int64:
        return self.n
