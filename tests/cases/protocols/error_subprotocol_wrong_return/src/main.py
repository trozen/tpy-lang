# A sub-protocol redeclaring a parent method with an incompatible return is
# no substitute for the parent: refused at its declaration.
from typing import Protocol
from tpy import int32


class Base(Protocol):
    def get(self) -> int32: ...


class Bad(Base, Protocol):  # tpyc: error(/Protocol 'Bad' does not conform to protocol 'Base': method 'get' has wrong signature: expected 'def get\(self\) -> int32', got 'def get\(self\) -> str'/)
    def get(self) -> str: ...


def use_base(b: Base) -> int32:
    return b.get() + 1


def use_bad(b: Bad) -> int32:
    return use_base(b)
