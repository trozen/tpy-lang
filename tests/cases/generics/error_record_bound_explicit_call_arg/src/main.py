# A bound broken in an explicit type argument of a generic function call.
from tpy import ValueType, int32


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


def make[T]() -> int32:
    return 1


def main() -> None:
    print(make[Box[list[int32]]]())  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'/)


main()
