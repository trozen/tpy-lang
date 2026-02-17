# Reassigned local (T* pointer-local) at last use gets auto-moved.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test() -> Int32:
    p = Point()
    p.x = 10
    p = Point()  # reassignment -> T* pointer-local
    p.x = 20
    return consume(p)  # last use -> std::move((*p))


def main():
    print(test())
