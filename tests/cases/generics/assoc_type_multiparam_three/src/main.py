# Multi-param inference is N-generic, not special-cased to 2: a 3-param
# protocol bound (Triple[A, B, C]) solves all three positionally from the
# conformer, each a distinct type (int/str/bool). A pairwise-only or
# off-by-one bug that a 2-param test can't reach would surface here.
from typing import Protocol


class Triple[A, B, C](Protocol):
    def a(self) -> A: ...
    def b(self) -> B: ...
    def c(self) -> C: ...


class Rec:
    x: int
    y: str
    z: bool

    def __init__(self, x: int, y: str, z: bool):
        self.x = x
        self.y = y
        self.z = z

    def a(self) -> int:
        return self.x

    def b(self) -> str:
        return self.y

    def c(self) -> bool:
        return self.z


def get_a[A, B, C, T: Triple[A, B, C]](t: T) -> A:      # tpyc: ok
    return t.a()


def get_b[A, B, C, T: Triple[A, B, C]](t: T) -> B:      # tpyc: ok
    return t.b()


def get_c[A, B, C, T: Triple[A, B, C]](t: T) -> C:      # tpyc: ok
    return t.c()


def main() -> None:
    r = Rec(9, "mid", True)
    print(get_a(r) + 1)     # A = int
    print(get_b(r))         # B = str
    print(get_c(r))         # C = bool


main()
