# Inverse guard for the sibling-referencing bound fix: when R is resolved to a
# type the conformer's method does NOT return, the bound must still REJECT.
# Ensures substituting the bound before the check does not over-accept.
from typing import Protocol
from tpy import int32, StrView


class Container[T](Protocol):
    def get(self) -> T: ...


class IntBox:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
    def get(self) -> int32:
        return self.v


def pick[R, T: Container[R]](x: T) -> R:
    return x.get()


def main() -> None:
    # IntBox.get() -> int32, so IntBox does not satisfy Container[StrView].
    print(pick[StrView, IntBox](IntBox(42)))   # tpyc: error(/does not satisfy bound/)


main()
