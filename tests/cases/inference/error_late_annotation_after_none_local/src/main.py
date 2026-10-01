# An annotation on a local the function already bound is refused: it goes on
# the first binding (mypy: Name "x" already defined).
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def bad() -> None:
    x = None
    # an annotation past the first binding
    x: Point = Point(1)  # tpyc: error(/'x' is already bound at line 16; an annotation goes on the first binding/)


bad()
