# Transitive mutation: outer forwards a tuple to inner that mutates it.
# Phase-2 propagation marks outer's tuple param mutated too, so
# const-inference does NOT fire for outer. Slots stay non-const.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def inner(p: tuple[T | None, T | None]) -> None:  # tpyc: ok
    a, b = p
    if a is not None:
        a.x = a.x + 100


def outer(p: tuple[T | None, T | None]) -> None:  # tpyc: ok
    # outer doesn't mutate p directly, but inner does -- transitive
    # mutation must propagate, otherwise const slots would be inferred
    # and break this call.
    inner(p)


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    outer((t1, t2))
    print(t1.x)


main()
