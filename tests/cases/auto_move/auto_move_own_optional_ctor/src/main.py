# Own[T] | None constructor param: auto-move at call site.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


class Wrapper:
    tag: Int32

    def __init__(self, p: Own[Point] | None, tag: Int32):
        if p is not None:
            self.tag = tag
        else:
            self.tag = Int32(-1)


def main():
    p = Point()
    p.x = Int32(1)
    p.y = Int32(2)
    w = Wrapper(p, Int32(42))
    print(w.tag)


main()
