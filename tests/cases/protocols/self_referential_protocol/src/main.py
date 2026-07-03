# A protocol may reference its own name (or another protocol, mutually) in a
# method signature; covers operator-dunder, recursive-return, and mutual shapes.
from __future__ import annotations
from typing import Protocol
from tpy import ValueType


class Addable(Protocol):
    def __add__(self, other: Addable) -> Addable: ...
    def value(self) -> int: ...


class Meters(ValueType):
    def __init__(self, v: int) -> None:
        self.v = v

    def __add__(self, other: Meters) -> Meters:
        return Meters(self.v + other.v)

    def value(self) -> int:
        return self.v


class Chainable(Protocol):
    def half(self) -> Chainable: ...
    def value(self) -> int: ...


class Num(ValueType):
    def __init__(self, v: int) -> None:
        self.v = v

    def half(self) -> Num:
        return Num(self.v // 2)

    def value(self) -> int:
        return self.v


class AProto(Protocol):
    def to_b(self) -> BProto: ...


class BProto(Protocol):
    def tag(self) -> str: ...
    def to_a(self) -> AProto: ...


class Ping(ValueType):
    def __init__(self) -> None:
        pass

    def to_b(self) -> Pong:
        return Pong()


class Pong(ValueType):
    def __init__(self) -> None:
        pass

    def tag(self) -> str:
        return "pong"

    def to_a(self) -> Ping:
        return Ping()


def combine(a: Addable, b: Addable) -> None:
    c = a + b
    print(c.value())


def chain(c: Chainable) -> None:
    print(c.half().value())


def bounce(a: AProto) -> None:
    print(a.to_b().tag())


def main() -> None:
    combine(Meters(30), Meters(12))
    chain(Num(8))
    bounce(Ping())


main()
