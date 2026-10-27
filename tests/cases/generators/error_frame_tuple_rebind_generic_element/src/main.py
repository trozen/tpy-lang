# A generator's rebound tuple owning an element of a generic type parameter
# is refused, as a plain function body's is: the tuple stays in storage form
# (its open `T` element is the instantiation's to hold), so the rebind needs
# storage of its own (BUGS.md#rebound-tuple-generic-element-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# the two never-consumed warnings are spurious: both parameters move into the
# tuple (BUGS.md#spurious-alias-warnings-select-and-own-literal)
def run[T](x: Own[T], y: Own[T]) -> Iterator[int32]:  # tpyc: warning(/Own\[T\] param 'x' is never consumed/) warning(/Own\[T\] param 'y' is never consumed/)
    p = (x, 0)
    yield p[1]
    p = (y, 1)  # tpyc: error(/the object the rebind of 'p' creates needs storage of its own/)
    yield p[1]


def main() -> None:
    print(list(run(Box(1), Box(2))))


main()
