# Payload types the sections instantiate their open `T` at.
from typing import Protocol

from tpy import ValueType, int32, nocopy


class Named(Protocol):
    def name(self) -> str: ...


class V(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def name(self) -> str:
        return "v"

    def __str__(self) -> str:
        return f"V({self.n})"


@nocopy
class P:
    # The reference instantiation: a silent copy of it would be a build error,
    # so a section that takes it holds the caller's object.
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def name(self) -> str:
        return "p"

    def __str__(self) -> str:
        return f"P({self.n})"


class C:
    # A copyable class: the constructor section's field store copies it (warned).
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"C({self.n})"
