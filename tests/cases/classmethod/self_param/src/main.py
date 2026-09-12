# `Self` in a classmethod PARAMETER resolves to the defining class, the same
# substitution its return type gets (a @staticmethod still rejects Self).
from typing import Self

from tpy import int32, Own


class Point:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def sum_of(cls, a: Self, b: Self) -> Own[Self]:
        return cls(a.x + b.x)


def main() -> None:
    p = Point(2)
    q = Point(5)
    r = Point.sum_of(p, q)
    print(r.x)


main()
