# An unpack of a field is rooted at the record local that owns it
# (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Callable
from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    t: tuple[P | None, int32]

    def __init__(self, x: int32) -> None:
        self.t = (P(x), x)


def f() -> Callable[[], int32]:
    h = H(5)
    a, n = h.t

    def g() -> int32:  # tpyc: error(/a closure that outlives 'f' captures 'a', which refers to an object in storage of 'f' itself/)
        if a is not None:
            return a.x
        return 0
    return g


def main() -> None:
    print(f()())


main()
