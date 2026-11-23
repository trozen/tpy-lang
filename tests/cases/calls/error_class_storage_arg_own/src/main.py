# Error: LANGUAGE_FEATURES "A class a generic function constructs by inference
# holds the storage form" -- `Crate(v)` meeting `Crate[T]` mismatches at `Own[P]`.
from typing import Callable
from tpy import Own, int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


class Crate[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Pair[A, B]:
    a: A
    b: B

    def __init__(self, a: A, b: B) -> None:
        self.a = a
        self.b = b


def mk() -> Own[P]:
    return P(9)


def wrap[T](v: Own[T]) -> Own[Crate[T]]:
    c = Crate(v)
    return c


# the class spelled at `T` itself is the slot's class at every instantiation
def wrap_spelled[T](v: Own[T]) -> Own[Crate[T]]:
    c = Crate[T](v)
    return c


def wrap_f[T](f: Callable[[], T]) -> Own[Crate[T]]:
    c = Crate(f())
    return c


# every class argument is compared, not the first one alone
def pair[A, B](a: Own[A], b: Own[B]) -> Own[Pair[A, B]]:
    c = Pair(a, b)
    return c


# the slot holding the storage form takes the class at `T` no more
def reverse[T](v: Own[T]) -> int32:
    c = Crate(v)
    d = Crate[T](v)
    c = d
    return 1


def peek[T](c: Crate[T]) -> int32:
    return 1


def crate_of[U](v: Own[U]) -> Own[Crate[U]]:
    return Crate[U](v)


# an owned storage-form value lent to a borrowed `Crate[T]`
def lend[T](v: Own[T]) -> int32:
    return peek[T](crate_of(v))


class Tray[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []


def tray_of[U](v: U) -> Own[Tray[U]]:
    return Tray[U]()


def pin_tray[T](t: Tray[T]) -> None:
    pass


# a pending instance pinned to `T` meets the class a value inferred
def pending_flow[T](f: Callable[[], T]) -> int32:
    t1 = Tray()
    pin_tray[T](t1)
    t2 = tray_of(f())
    t2 = t1
    return len(t2.items)


def main() -> None:
    # neighbours, accepted (no diagnostic in diag.txt): at `T = P` and a value
    # `T` the two classes are one
    print(wrap[P](P(1)).v.n, wrap[int32](2).v)
    print(wrap_spelled[Own[P]](P(3)).v.n)
    # refused at the instantiation, naming the return in 'wrap'
    a = wrap[Own[P]](P(1))  # tpyc: error(/Type mismatch in return value: expected Crate\[Own\[P\]\], got Crate\[P\] \(in 'wrap' \(line 34\), instantiated here\); spell the class argument \(`Crate\[T\]\(...\)`\)/)
    print(a.v.n)
    # a CPython-valid inferred call: the owning callable binds `T = Own[P]`
    own_mk: Callable[[], Own[P]] = mk
    print(wrap_f(own_mk).v.n)  # tpyc: error(/expected Crate\[Own\[P\]\], got Crate\[P\] \(in 'wrap_f' \(line 45\)/)
    print(pair[int32, Own[P]](1, P(2)).a)  # tpyc: error(/expected Pair\[int32, Own\[P\]\], got Pair\[int32, P\].*`Pair\[A, B\]\(...\)`/)
    print(reverse[Own[P]](P(3)))  # tpyc: error(/Type mismatch in reassignment to 'c': expected Crate\[P\], got Crate\[Own\[P\]\] \(in 'reverse' \(line 58\)/)
    print(lend[Own[P]](P(5)))  # tpyc: error(/expected Crate\[Own\[P\]\], got Crate\[P\] \(in 'lend' \(line 72\)/)
    print(pending_flow[Own[P]](lambda: P(6)))  # tpyc: error(/Type mismatch in reassignment to 't2': expected Tray\[P\], got Tray\[Own\[P\]\] \(in 'pending_flow' \(line 95\)/)


main()
