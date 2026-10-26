# A local aliasing an unpack target is rooted where the target is: the local
# tuple's owned element (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Callable
from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x = self.x + 100


def mk(x: int32) -> tuple[Own[P] | None, int32]:
    return (P(x), x)


def f() -> Callable[[], int32]:
    t = mk(5)
    a, n = t
    c, m = t
    b = a

    def g() -> int32:  # tpyc: error(/a closure that outlives 'f' captures 'b', which refers to an object in storage of 'f' itself/)
        if b is not None:
            return b.x
        return 0
    if c is not None:
        c.bump()
    return g


def main() -> None:
    print(f()())


main()
