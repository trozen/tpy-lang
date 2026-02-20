# Own[T] | None narrowed field access: codegen regression test.
# Known bug: generates p.x instead of (*p).x for std::optional<Point>.
# No expected/output.txt until the narrowing codegen is fixed.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def use_optional(p: Own[Point] | None) -> Int32:
    if p is not None:
        return p.x
    return Int32(0)


def main():
    pt = Point()
    pt.x = Int32(42)
    pt.y = Int32(7)
    print(use_optional(pt))


main()
