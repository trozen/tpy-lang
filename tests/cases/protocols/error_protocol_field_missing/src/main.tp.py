from tpy import Int32
from typing import Protocol


class HasValue(Protocol):
    value: Int32


class NoValue:
    other: Int32

    def __init__(self, x: Int32):
        self.other = x


def get_value[T: HasValue](item: T) -> Int32:
    return item.value


def main() -> None:
    nv = NoValue(10)
    print(get_value(nv))  # tpyc: error(/Cannot infer type arguments/)


main()
