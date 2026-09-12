# Inverse guard for the class-level-sibling method bound: when the class param
# resolves to a type the conformer does not match, the bound must produce a
# LOCATED "does not satisfy bound" diagnostic -- not an unlocated internal crash
# (the failure mode the merged-subst fix prevents).
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


class Runner[R]:
    def pick[T: Container[R]](self, x: T) -> R:
        return x.get()


def main() -> None:
    # R = StrView, but IntBox.get() -> int32, so IntBox does not satisfy Container[StrView].
    print(Runner[StrView]().pick[IntBox](IntBox(42)))   # tpyc: error(/does not satisfy bound/)


main()
