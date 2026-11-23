# Error: a member call with the method's own type parameters on a pending
# instance, before the class's type arguments are known, is refused.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[P]:
    return P(1)


def check_pair(a: Own[P], b: Own[P]) -> None:
    sink: list[P] = [a, b]
    sink[0].n = 99
    print(sink[0].n, sink[1].n)


class Holder[T]:
    f: Callable[[], T]
    check: Callable[[T, T], None]

    def __init__(self, f: Callable[[], T], check: Callable[[T, T], None]) -> None:
        self.f = f
        self.check = check

    def make(self) -> T:
        return self.f()

    def pair(self, a: T, b: T) -> None:
        self.check(a, b)


class Duo[A, B]:
    def __init__(self) -> None:
        pass

    def pin_b(self, y: B) -> None:
        pass

    def dup_from[T](self, x: A, h: Holder[T]) -> None:
        v = h.make()
        h.pair(v, v)


def run_early[T](h: Holder[T]) -> None:
    d = Duo()
    # `T` of `dup_from` is never inferred here: its copy of `v` would go unseen
    d.dup_from(1, h)  # tpyc: error(/Cannot call generic method 'dup_from' on 'd' before the type arguments of 'Duo' are known; call a constraining method first or spell the class type arguments: Duo\[A, B\]\(...\)/)
    d.pin_b(7)


def main() -> None:
    own_mk: Callable[[], Own[P]] = mk
    own_check: Callable[[Own[P], Own[P]], None] = check_pair
    h = Holder[Own[P]](own_mk, own_check)
    run_early(h)


main()
