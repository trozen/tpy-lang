from typing import Protocol
from tpy import ValueType, int32


class C:
    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n

    def __str__(self) -> str:
        return f"C({self.n})"


class V(ValueType):
    def __init__(self, s: str) -> None:
        self.s = s

    def get(self) -> int32:
        return len(self.s)

    def __str__(self) -> str:
        return self.s


class HasGet(Protocol):
    def get(self) -> int32: ...


def make_str(k: int32) -> str:
    return "abcd" * k
