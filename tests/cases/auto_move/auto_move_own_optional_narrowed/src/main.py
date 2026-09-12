# Own[T] | None narrowed field access: after narrowing, (*p).x unwraps std::optional.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


def use_optional(p: Own[Point] | None) -> int32:
    if p is not None:
        return p.x
    return int32(0)


def main():
    pt = Point()
    pt.x = int32(42)
    pt.y = int32(7)
    print(use_optional(pt))


main()
