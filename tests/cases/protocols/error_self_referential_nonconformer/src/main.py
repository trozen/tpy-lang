# The coinductive cycle guard must not over-accept: a record missing the
# self-referential protocol's method is still rejected.
from typing import Protocol
from tpy import ValueType


class Addable(Protocol):
    def __add__(self, other: Addable) -> Addable: ...


class Blob(ValueType):
    def __init__(self) -> None:
        pass


def add(a: Addable, b: Addable) -> None:
    pass


def main() -> None:
    add(Blob(), Blob())  # tpyc: error(/does not conform to protocol Addable/)


main()
