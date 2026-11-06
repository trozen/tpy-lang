# A protocol combining two parents that disagree on a member: its member is
# the first parent's (Python's MRO), which is no substitute for the second.
from typing import Protocol
from tpy import int32


class A(Protocol):
    def get(self) -> int32: ...


class B(Protocol):
    def get(self) -> str: ...


class C(A, B, Protocol):  # tpyc: error(/Protocol 'C' does not conform to protocol 'B': method 'get' has wrong signature: expected 'def get\(self\) -> str', got 'def get\(self\) -> int32'/)
    pass


def use_b(b: B) -> str:
    return b.get() + "!"


def use_c(c: C) -> str:
    return use_b(c)
