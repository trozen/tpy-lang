from tpy import Int32
from typing import Protocol


class HasValue(Protocol):
    value: Int32


# Record has 'value' field but wrong type (bool instead of Int32)
class WrongType:
    value: bool

    def __init__(self, v: bool):
        self.value = v


def get_value[T: HasValue](item: T) -> Int32:
    return item.value


def main() -> None:
    wt = WrongType(True)
    print(get_value(wt))  # tpyc: error(/Cannot infer type arguments/)


main()
