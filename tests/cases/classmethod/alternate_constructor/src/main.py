# @classmethod as an alternate constructor: `cls(...)` builds the defining
# class, and the shared ClassVar counter is observed across calls (each factory
# call returns a freshly constructed object, so there is nothing to alias).
from typing import ClassVar, Final, Self

from tpy import Int32, Own


class Point:
    ORIGIN: Final[Int32] = 0
    created: ClassVar[Int32] = 0

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    @classmethod
    def origin(cls) -> Own[Self]:  # tpyc: ok
        cls.created += 1
        return cls(cls.ORIGIN, cls.ORIGIN)

    @classmethod
    def diagonal(cls, n: Int32) -> Own[Self]:
        return cls.scaled(n, 1)

    @staticmethod
    def scaled(n: Int32, k: Int32) -> Own["Point"]:
        return Point(n * k, n * k)


def main() -> None:
    p = Point.origin()
    print(p.x, p.y, Point.created)

    q = Point.diagonal(3)
    print(q.x, q.y, Point.created)

    # The ClassVar counter is class-level state shared across calls.
    r = Point.origin()
    print(r.x, Point.created)

    # The returned object is owned, so mutating it is visible here.
    r.x = 9
    print(r.x)


main()
