# Regression: unary operator dispatch (`-x`) on a generic wrapper whose __neg__
# has `[T: Comparable]` shadowing class T. The class-shadowed bound must be
# enforced at the resolve_unaryop dispatch site.
from tpy import Comparable


class NotComparable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Wrap[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def __neg__[T: Comparable](self) -> Wrap[T]:
        return self


def main() -> None:
    a = Wrap(NotComparable(1))
    b = -a  # tpyc: error(/requires type parameter 'T' to satisfy 'Comparable'/)


main()
