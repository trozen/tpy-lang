# A pending instance infers no `Own` across statements: an owning callable
# after a statement that settled `A = P` is refused, naming the spelling.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[P]:
    return P(1)


class Duo[A, B]:
    def __init__(self) -> None:
        pass

    def pop_a(self, xs: list[A]) -> readonly[A]:
        return xs.pop()

    def set_f(self, f: Callable[[], A]) -> None:
        pass

    def pin_b(self, y: B) -> None:
        pass


def run(flag: bool) -> None:
    own_mk: Callable[[], Own[P]] = mk
    xs = [P(10)]
    d = Duo()
    r = d.pop_a(xs) if flag else xs[0]
    # `A = P` is settled: the owning callable would need `A = Own[P]`
    d.set_f(own_mk)  # tpyc: error(/Cannot infer 'Own' for type argument 'A' of 'Duo' from a later call on 'd'; spell the class type arguments: Duo\[Own\[P\], B\]\(\.\.\.\)/)
    d.pin_b(7)
    xs[0].n = 99
    print("pending", r.n)


def main() -> None:
    run(False)


main()
