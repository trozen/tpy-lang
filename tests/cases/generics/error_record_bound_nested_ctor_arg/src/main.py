# A bound broken in a nested explicit type argument of a construction.
from tpy import ValueType, int32


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Outer[T]:
    def __init__(self) -> None:
        pass


def main() -> None:
    o = Outer[Box[list[int32]]]()  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'/)


main()
