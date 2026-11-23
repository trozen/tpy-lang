# A member call with the method's own type parameters on a once-pending
# instance: after a constraining call, or with the class arguments spelled,
# it binds the method's `T` as the resolved call does, and `T = Own[P]`'s
# copy of `v` is warned at the instantiation. A borrow a pending member
# call returns holds the instance as the twin's does, and the spelled
# remedy of the pending-`Own` refusal aliases as its twin.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32, readonly
from fixtures import P


def mk_p() -> Own[P]:
    return P(1)


def check_pair(a: Own[P], b: Own[P]) -> None:
    sink: list[P] = [a, b]
    sink[0].n = 99
    print("pending-method", sink[0].n, sink[1].n)


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


def run_late[T](h: Holder[T]) -> None:
    # the constraining call first: `dup_from` is called on `Duo[int32, int32]`
    d = Duo()
    d.pin_b(7)
    d.dup_from(1, h)


def run_spelled[T](h: Holder[T]) -> None:
    # the class arguments spelled: the remedy the pending guard names
    d = Duo[int32, int32]()
    d.dup_from(1, h)
    d.pin_b(7)


class Shelf[A, B]:
    a: list[A]
    b: list[B]

    def __init__(self) -> None:
        self.a = []
        self.b = []

    def put(self, x: A) -> A:
        self.a.append(x)  # tpyc: warning(/may copy A into owned storage/)
        return self.a[-1]

    def add_b(self, y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)


def borrow_pending() -> None:
    # the result of a member call on a pending instance borrows it, as the
    # twin's does: a later mutating call warns
    p = P(1)
    d = Shelf()
    r = d.put(p)
    r.n = 7
    d.add_b(7)  # tpyc: warning(/Mutation of 'd' while borrowed \('add_b' may invalidate references\)/)
    print("pending-borrow", d.a[0].n, r.n)


def borrow_twin() -> None:
    p = P(1)
    d = Shelf[P, int32]()
    r = d.put(p)
    r.n = 7
    d.add_b(7)  # tpyc: warning(/Mutation of 'd' while borrowed \('add_b' may invalidate references\)/)
    print("pending-borrow-twin", d.a[0].n, r.n)


class Pop[A, B]:
    def __init__(self) -> None:
        pass

    def pop_a(self, xs: list[A]) -> readonly[A]:
        return xs.pop()

    def set_f(self, f: Callable[[], A]) -> None:
        pass

    def pin_b(self, y: B) -> None:
        pass


def own_spelled(flag: bool) -> None:
    # the remedy the pending-`Own` refusal names: with the class arguments
    # spelled, `A = Own[P]` takes the owning callable after `pop_a`
    own_mk: Callable[[], Own[P]] = mk_p
    xs = [P(10)]
    d = Pop[Own[P], int32]()
    r = d.pop_a(xs) if flag else xs[0]
    d.set_f(own_mk)  # tpyc: ok
    d.pin_b(7)
    # `r` aliases the list's element
    xs[0].n = 99
    print("pending-own-spelled", r.n)

def main() -> None:
    own_mk: Callable[[], Own[P]] = mk_p
    own_check: Callable[[Own[P], Own[P]], None] = check_pair
    h = Holder[Own[P]](own_mk, own_check)
    # the copy of `v` at `T = Own[P]`, warned at each instantiation
    run_late(h)  # tpyc: warning(/copies P into owned storage/)
    run_spelled(h)  # tpyc: warning(/copies P into owned storage/)
    borrow_pending()
    borrow_twin()
    own_spelled(False)
