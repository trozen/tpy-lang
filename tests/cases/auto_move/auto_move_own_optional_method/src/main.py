# Own[T] | None method param: auto-move at call site, no copy warning.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


class Container:
    val: int32

    def take(self, p: Own[Point] | None) -> None:
        if p is not None:
            self.val = int32(1)
        else:
            self.val = int32(0)


def main():
    c = Container()
    c.val = int32(-1)
    p = Point()
    p.x = int32(3)
    p.y = int32(4)
    c.take(p)
    print(c.val)


main()
