# An escaping def capturing a rebound `Own[P]` unpack target would keep its
# pointer into the parameter: BUGS.md#ptr-tuple-capture-and-binder-rejects.
from typing import Callable
from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(t: tuple[Own[P], int32], k: bool) -> Callable[[], int32]:
    a, n = t
    c, m = t
    if k:
        a = P(9)

    def g() -> int32:  # tpyc: error(/a closure that outlives 'f' captures 'a', which refers to an object in storage of 'f' itself.*bind a copy \(`a0 = copy\(a\)`/)
        return a.x + n
    print(c.x)
    return g


def main() -> None:
    g = f((P(5), 2), False)
    print(g())


main()
