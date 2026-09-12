# Rvalue tuple literal targeting a pointer-form Optional slot. The old
# codegen emitted `&(Point(1))` which GCC rejects as "address of rvalue".
# The fix routes rvalue elements through ::tpy::tuple_value_to_borrow,
# which materializes a value-tuple temp (lifetime extends to end of full
# expression) and addresses its slots inside.
from tpy import int32


class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def show(p: tuple[Point | None, int32]) -> None:
    a, n = p
    if a is not None:
        print(a.x)
    else:
        print("none")
    print(n)


def show2(p: tuple[Point | None, Point | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("none")
    if b is not None:
        print(b.x)
    else:
        print("none")


def main() -> None:
    # All-rvalue tuple literal at call site.
    show((Point(1), 42))
    # Mixed: rvalue + None + lvalue at the same call site.
    p = Point(7)
    show((Point(2), 99))
    show((None, 5))
    show((p, 11))
    # Helper's pointer-pass-through path: rvalue + lvalue elements both
    # land in pointer-form Optional slots, so the source value-tuple has
    # mixed slot shapes (T value at slot 0, T* at slot 1).
    show2((Point(20), p))
    show2((p, Point(30)))


main()
