# An escaping def capturing an `Own[P] | None` unpack target the tuple still
# owns would keep a pointer into it: BUGS.md#ptr-tuple-capture-and-binder-rejects.
from typing import Callable
from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(t: tuple[Own[P] | None, int32]) -> Callable[[], int32]:
    a, n = t
    c, m = t

    def g() -> int32:  # tpyc: error(/a closure that outlives 'f' captures 'a', which refers to an object in storage of 'f' itself.*narrow it and bind a copy/)
        if a is not None:
            return a.x + n
        return n
    if c is not None:
        print(c.x)
    return g


def main() -> None:
    g = f((P(5), 2))
    print(g())


main()
