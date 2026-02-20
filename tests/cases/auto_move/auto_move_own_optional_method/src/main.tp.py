# Own[T] | None method param: auto-move at call site, no copy warning.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


class Container:
    val: Int32

    def take(self, p: Own[Point] | None) -> None:
        if p is not None:
            self.val = Int32(1)
        else:
            self.val = Int32(0)


def main():
    c = Container()
    c.val = Int32(-1)
    p = Point()
    p.x = Int32(3)
    p.y = Int32(4)
    c.take(p)
    print(c.val)


main()
