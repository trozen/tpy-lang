# A pending instance bound over several statements settles each
# statement's bindings to the types they name (no `Own` inferred across
# statements), so the result is the same in either statement order.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32
from fixtures import Box, P, mk


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


class Duo[A, B]:
    f: Callable[[], A] | None
    b: list[B]

    def __init__(self) -> None:
        self.f = None
        self.b = []

    def set_f(self, f: Callable[[], A]) -> None:
        self.f = f

    def add_b(self, y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)

    def take_list(self, xs: list[A]) -> None:
        xs.clear()

    def finish(self, c: Cell[A], y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)


def make_duo[T](f: Callable[[], T]) -> Own[Duo[T, int32]]:
    # the exact `A = T` an owning callable gave survives the return's settle
    d = Duo()  # tpyc: type(Duo[T, int32])
    d.set_f(f)
    return d


def make_duo_fb[T](f: Callable[[], T]) -> Own[Duo[T, int32]]:
    d = Duo()
    d.set_f(f)
    d.add_b(7)
    return d


def make_duo_bf[T](f: Callable[[], T]) -> Own[Duo[T, int32]]:
    d = Duo()
    d.add_b(7)
    d.set_f(f)
    return d


def make_lf[T](c: Cell[T], xs: list[T]) -> Own[Duo[T, int32]]:
    # the class-argument `A = T` of `list[T]` survives the next statement
    d = Duo()
    d.take_list(xs)
    d.finish(c, 7)  # tpyc: ok
    return d


def make_fl[T](c: Cell[T], xs: list[T]) -> Own[Duo[T, int32]]:
    d = Duo()
    d.finish(c, 7)  # tpyc: ok
    d.take_list(xs)
    return d


class Acc[A, B]:
    a: list[A]
    b: list[B]

    def __init__(self) -> None:
        self.a = []
        self.b = []

    def take(self, x: A) -> None:
        self.a.append(x)  # tpyc: warning(/may copy A into owned storage/)

    def take_list(self, xs: list[A]) -> None:
        xs.clear()

    def call(self, f: Callable[[], A]) -> None:
        x = f()
        print("pending-mixed", x)

    def finish(self, c: Cell[A], y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)

    def pin(self, y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)


def lone_list[T](xs: list[T]) -> Own[Acc[T, int32]]:
    # a neutral `A = T` alone forms `Acc[T, int32]`, the return's type
    d = Acc()
    d.take_list(xs)  # tpyc: ok
    return d


def storage_fl[T](c: Cell[T], x: T) -> Own[Acc[T, int32]]:
    # the storage form `Unowned(T)` of a one-call `Box(x)` settles as `T`,
    # so the class argument's `T` agrees with it in either order
    b = Box(x)
    d = Acc()
    d.finish(c, 7)
    d.take_list(b.items)  # tpyc: ok
    return d


def storage_lf[T](c: Cell[T], x: T) -> Own[Acc[T, int32]]:
    b = Box(x)
    d = Acc()
    d.take_list(b.items)
    d.finish(c, 7)  # tpyc: ok
    return d


def mixed_bf[T](f: Callable[[], T], g: Callable[[], Own[T]]) -> None:
    # a borrowing and an owning callable over an open `T` settle as one `T`
    d = Acc()
    d.call(f)
    d.call(g)  # tpyc: ok
    d.pin(7)


def mixed_fb[T](f: Callable[[], T], g: Callable[[], Own[T]]) -> None:
    d = Acc()
    d.call(g)
    d.call(f)  # tpyc: ok
    d.pin(7)


def take_twice[T](c: Cell[T], x: T) -> Own[Acc[T, int32]]:
    # `x` taken twice before the class argument: at `T = Own[P]` each take
    # copies, warned at the instantiation
    d = Acc()
    d.take(x)
    d.take(x)
    d.finish(c, 7)
    return d


def mk42() -> int32:
    return 42


def main_settle() -> None:
    xs = list[Own[P]]()
    xs.append(P(2))
    # `xs` is lent: the callee's `clear` reaches the caller's list
    lone = lone_list(xs)
    print("pending-lone", len(lone.b), len(xs))
    c = Cell[Own[P]](P(1))
    fl = storage_fl(c, P(2))
    lf = storage_lf(c, P(3))
    print("pending-storage", fl.b[0], lf.b[0])
    f: Callable[[], int32] = mk42
    mixed_bf(f, f)
    mixed_fb(f, f)
    tw = take_twice(c, P(2))  # tpyc: warning(/copies P into owned storage/)
    print("pending-twice", len(tw.a), len(tw.b))


def main() -> None:
    own_mk: Callable[[], Own[P]] = mk
    d = make_duo(own_mk)
    fb = make_duo_fb(own_mk)
    bf = make_duo_bf(own_mk)
    print("pending-settle", len(d.b), fb.b[0], bf.b[0])
    c = Cell[Own[P]](P(1))
    xs = list[Own[P]]()
    xs.append(P(2))
    # `xs` is lent: the callee's `clear` reaches the caller's list
    lf = make_lf(c, xs)
    print("pending-lf", lf.b[0], len(xs))
    ys = list[Own[P]]()
    ys.append(P(3))
    fl = make_fl(c, ys)
    print("pending-fl", fl.b[0], len(ys))
    zs = [P(4)]
    lp = make_lf(Cell(P(5)), zs)
    print("pending-p", lp.b[0], len(zs))
    main_settle()
