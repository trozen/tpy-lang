# List literal contextual element-type widening from annotations.
# When LHS annotation is wider than the literal's inferred type,
# the literal adopts the annotation's element type.
from tpy import int32, int64, Own


class Rect:
    w: int32
    h: int32

class Circle:
    r: int32


def make_optional_list() -> Own[list[int32 | None]]:
    return [int32(1), None, int32(3)]


def main():
    # Homogeneous literal, wider annotation (Optional)
    a: list[int32 | None] = [int32(1), int32(2)]
    print(len(a))

    # None-only literal
    b: list[int32 | None] = [None]
    print(len(b))

    # Mixed literal: int32 and None
    c: list[int32 | None] = [int32(1), None, int32(3)]
    print(len(c))

    # Empty literal with union element type
    d: list[int32 | None] = []
    d.append(int32(42))
    d.append(None)
    print(len(d))

    # Return type context with union element type
    e: list[int32 | None] = make_optional_list()
    print(len(e))

    # Mutation on widened type
    a.append(None)
    print(len(a))

    # Int literals in union annotation
    f: list[int32 | None] = [1, None, 3]
    print(len(f))

    # Int literals in wider numeric type
    g: list[int64] = [1, 2, 3]
    print(len(g))

    # Union of records: lvalue and rvalue mixing
    r = Rect()
    r.w = 10
    r.h = 20
    shapes: list[Rect | Circle] = [r, Circle()]
    print(len(shapes))

    # Union of records: all rvalues
    shapes2: list[Rect | Circle] = [Rect(), Circle()]
    print(len(shapes2))


main()
