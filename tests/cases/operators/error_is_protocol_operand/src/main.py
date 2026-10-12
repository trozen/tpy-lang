# `is` with a protocol-typed operand is not supported yet
# (BUGS.md#identity-protocol-rejects).
from typing import Protocol


class Named(Protocol):
    def name(self) -> str: ...


class A:
    def name(self) -> str:
        return "a"


def same(x: Named, y: Named) -> bool:
    return x is y  # tpyc: error(/a protocol operand \('Named'\) is not supported yet/)


a = A()
print(same(a, a))
