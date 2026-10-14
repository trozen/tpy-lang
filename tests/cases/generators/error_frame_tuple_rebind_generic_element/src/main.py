# A generator's rebound tuple owning an element of a generic type parameter
# is refused, as a plain function body's is: the pointer tuple's owned
# element needs a bare pointer, which `T` has only once its instantiation
# says it is a reference type (BUGS.md#rebound-tuple-generic-element-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# the two never-consumed warnings are spurious: both parameters move into the
# tuple (BUGS.md#spurious-alias-warnings-select-and-own-literal)
def run[T](x: Own[T], y: Own[T]) -> Iterator[int32]:  # tpyc: warning(/Own\[T\] param 'x' is never consumed/) warning(/Own\[T\] param 'y' is never consumed/)
    p = (x, 0)  # tpyc: error(/rebinding 'p' while it owns element 0, a generic type parameter, is not yet supported here/)
    yield p[1]
    p = (y, 1)
    yield p[1]


def main() -> None:
    print(list(run(Box(1), Box(2))))


main()
