from tpy import int32
from typing import Protocol


class HasValue(Protocol):
    value: int32


class NoValue:
    other: int32

    def __init__(self, x: int32):
        self.other = x


def get_value[T: HasValue](item: T) -> int32:
    return item.value


def main() -> None:
    nv = NoValue(10)
    print(get_value(nv))  # tpyc: error(/Cannot infer type arguments/)


main()
