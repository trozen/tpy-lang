# The records and helpers the walrus_reseat sections share.
from typing import Iterator

from tpy import Own, ReturnException, int32, nocopy


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> int32:
        self.n += 1000
        return self.n


class B:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class T:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        return self.n > 0


class W:
    a: A

    def __init__(self, a: Own[A]) -> None:
        self.a = a


class H:
    a: A
    b: A
    bb: B
    xs: list[A]
    o: A | None
    ps: list[tuple[A, int32]]
    pair: tuple[int32, A]

    def __init__(self) -> None:
        self.a = A(1)
        self.b = A(2)
        self.bb = B(5)
        self.xs = [A(3), A(4)]
        self.o = A(6)
        self.ps = [(A(7), 70)]
        self.pair = (8, A(8))

    def pick(self) -> A:
        return self.b


class Ring:
    items: list[A]
    head: A | None

    def __init__(self) -> None:
        self.items = [A(1), A(2), A(3), A(4)]
        self.head = A(0)

    def after(self, a: A) -> A | None:
        i = 0
        while i < len(self.items) - 1:
            if self.items[i] is a:
                return self.items[i + 1]
            i += 1
        return None


class Err(Exception, ReturnException):
    pass


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 0

    def __exit__(self, kind, value, tb) -> None:
        pass


class OwnCtx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> Own[A]:
        return A(1)

    def __exit__(self, kind, value, tb) -> None:
        pass


def show(x: A | B) -> int32:
    if isinstance(x, A):
        return x.n
    return x.s


def bump(x: A | B) -> None:
    if isinstance(x, A):
        x.n += 10
    else:
        x.s += 10


def steps() -> Iterator[Own[A]]:
    yield A(5)
    yield A(6)


def make_pair(n: int32) -> tuple[int32, Own[A]]:
    return (n, A(n))


def take(s: Own[str]) -> None:
    print("live_self take", s)


def take_list(xs: Own[list[int32]]) -> Own[list[int32]]:
    return xs


def size(xs: list[int32]) -> int32:
    return len(xs)
