# Verify the no-copy property of tuple[T | None, ...] for record T:
# elements are accessed through T*, so mutations through the destructured
# / passed-along references reach the original. The mutation pattern is
# the load-bearing assertion -- if codegen accidentally copied an element
# on read, t1.x would not reflect the bump.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def both(a: T, b: T) -> tuple[T | None, T | None]:
    return (a, b)


def bump_first(p: tuple[T | None, T | None]) -> None:
    a, _ = p
    if a is not None:
        a.x = a.x + 100


def main() -> None:
    t1 = T(1)
    t2 = T(2)

    # Return-side: mutation through destructured pointer reaches t1.
    a, b = both(t1, t2)
    if a is not None:
        a.x = 10
    print(t1.x)  # 10

    # Param-side: bump_first mutates t1 through the pointer-form tuple param.
    bump_first((t1, t2))
    print(t1.x)  # 110

    # Pass-through: returned tuple flows directly into another function;
    # the mutation reaches the original through both boundaries.
    bump_first(both(t1, t2))
    print(t1.x)  # 210


main()
