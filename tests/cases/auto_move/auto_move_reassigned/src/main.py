# Reassigned local (T* pointer-local) at last use gets auto-moved.
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def test() -> int32:
    p = Point()
    p.x = 10
    p = Point()  # reassignment -> T* pointer-local
    p.x = 20
    return consume(p)  # last use -> std::move((*p))


def main():
    print(test())
