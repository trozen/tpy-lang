# An unpack of a tuple alias is rooted at the aliased local tuple's owned
# element (BUGS.md#ptr-tuple-capture-and-binder-rejects).
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
    u = t
    a, n = u
    c, m = t

    def g() -> int32:  # tpyc: error(/a closure that outlives 'f' captures 'a', which refers to an object in storage of 'f' itself/)
        if a is not None:
            return a.x
        return 0
    if c is not None:
        c.bump()
    return g


def main() -> None:
    print(f()())


main()
