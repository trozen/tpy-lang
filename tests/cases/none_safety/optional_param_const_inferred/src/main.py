# Single `T | None` param with read-only body is inferred const: the C++
# spelling becomes `const T* p` instead of `T* p`. Mirrors the existing
# `T -> const T&` inference for plain record params.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def show(p: T | None) -> None:  # tpyc: ok
    if p is not None:
        print(p.x)


def bump(p: T | None) -> None:  # tpyc: ok
    # mutates through p -- stays as mutable T*
    if p is not None:
        p.x = p.x + 100


def main() -> None:
    t = T(1)
    show(t)
    show(None)
    bump(t)
    print(t.x)


main()
