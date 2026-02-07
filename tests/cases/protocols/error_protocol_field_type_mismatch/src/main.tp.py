from tpy import Int32, Bool
from typing import Protocol


class HasValue(Protocol):
    value: Int32


# Record has 'value' field but wrong type (Bool instead of Int32)
class WrongType:
    value: Bool

    def __init__(self, v: Bool):
        self.value = v


def get_value[T: HasValue](item: T) -> Int32:
    return item.value


def main() -> None:
    wt = WrongType(True)
    print(get_value(wt))  # tpyc: error(/Cannot infer type arguments/)


main()
