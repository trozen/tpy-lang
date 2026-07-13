# An @export class declaring a @dynamic protocol base is rejected: the virtual
# C++ base's vptr displaces the payload layout the CPython instance embedding
# relies on (Instance<T> casts).
# tpy: ext_module
from typing import Protocol
from tpy import Int64, dynamic
from tpy.extern import export


@dynamic
class Countable(Protocol):
    def count(self) -> Int64: ...


@export
class Bag(Countable):  # tpyc: error(/implementing @dynamic protocol 'Countable' cannot be exposed/)
    def __init__(self, n: Int64):
        self.n = n

    def count(self) -> Int64:
        return self.n
