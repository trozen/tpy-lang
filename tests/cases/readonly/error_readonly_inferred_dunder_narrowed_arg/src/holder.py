# `Holder.__rand__` never writes `self`, so Phase 2 infers it const; it
# still writes its parameter.
from tpy import int32


class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def __rand__(self, x: Item) -> int32:
        x.v = 44
        return x.v
