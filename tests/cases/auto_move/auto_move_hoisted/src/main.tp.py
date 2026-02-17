# If/else branch variable (T* pointer-local) at last use gets auto-moved.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(cond: bool) -> Int32:
    if cond:
        p = Point()
        p.x = 42
    else:
        p = Point()
        p.x = 99
    # p is reassigned (assigned in both branches) -> T* pointer-local
    return consume(p)  # last use -> std::move((*p))


def main():
    print(test(True))
    print(test(False))
