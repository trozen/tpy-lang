# A generic type-param bound that names a sibling type parameter
# ([R, T: Container[R]]) must be checked with R resolved. Regression guard:
# the raw Container[R] was previously checked with R unbound, wrongly
# rejecting a conforming argument. Two instantiations prove R actually varies.
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


class StrBox:
    s: StrView
    def __init__(self, s: StrView) -> None:
        self.s = s
    def get(self) -> StrView:
        return self.s


def pick[R, T: Container[R]](x: T) -> R:   # tpyc: ok
    return x.get()


def main() -> None:
    print(pick[int32, IntBox](IntBox(42)))       # tpyc: ok
    print(pick[StrView, StrBox](StrBox("hi")))   # tpyc: ok


main()
