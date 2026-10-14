# Reached through the three-segment module head `pkg.sub.mod.`.
from enum import Enum

from tpy import Own


class Outer:
    class Inner:
        v: int

        def __init__(self, v: int) -> None:
            self.v = v * 10

    class Kind(Enum):
        A = 5
        B = 6


def make_inner(v: int) -> Own[Outer.Inner]:
    i = Outer.Inner(v)
    return i


def kind_b() -> Outer.Kind:
    return Outer.Kind.B
