# A bounded record spelled with a type argument breaking its bound, never constructed.
from tpy import ValueType, int32


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


def use(b: Box[list[int32]]) -> int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'/)
    return 1
