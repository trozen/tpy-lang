# A sub-protocol dropping a parameter default its parent declares rejects a
# call the parent admits, so it is no substitute for the parent.
from typing import Protocol
from tpy import int32


class Base(Protocol):
    def f(self, x: int32 = 7) -> int32: ...


class Child(Base, Protocol):  # tpyc: error(/Protocol 'Child' does not conform to protocol 'Base': method 'f' has wrong signature: expected 'def f\(self, int32 = \.\.\.\) -> int32', got 'def f\(self, int32\) -> int32'/)
    def f(self, x: int32) -> int32: ...


def use_base(b: Base) -> int32:
    return b.f()


def use_child(c: Child) -> int32:
    return use_base(c)
