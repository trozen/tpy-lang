# A by-value lambda the body only calls still counts as outliving it
# (BUGS.md#ptr-tuple-capture-and-binder-rejects; TODO.md "A `Callable` parameter the callee only calls never outlives the call").
from typing import Callable
from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    c, m = t
    g: Callable[[], int32] = lambda: a.x + n if a is not None else n  # tpyc: error(/a closure that outlives 'f' captures 'a'/)
    if c is not None:
        print(c.x)
    return g()


def main() -> None:
    print(f((P(5), 2)))


main()
