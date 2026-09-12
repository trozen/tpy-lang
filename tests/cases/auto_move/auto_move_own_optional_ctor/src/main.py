# Own[T] | None constructor param: auto-move at call site.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


class Wrapper:
    tag: int32

    def __init__(self, p: Own[Point] | None, tag: int32):
        if p is not None:
            self.tag = tag
        else:
            self.tag = int32(-1)


def main():
    p = Point()
    p.x = int32(1)
    p.y = int32(2)
    w = Wrapper(p, int32(42))
    print(w.tag)


main()
