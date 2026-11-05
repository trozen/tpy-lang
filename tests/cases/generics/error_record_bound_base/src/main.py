# A base class spelled with a type argument breaking its declared bound.
from tpy import ValueType, int32


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class D(Box[list[int32]]):  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'/)
    pass
