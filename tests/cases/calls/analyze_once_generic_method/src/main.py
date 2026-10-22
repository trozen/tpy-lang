# A generic method's argument is analyzed once: method-parameter
# inference reads its one analysis and the receive takes it, so a
# consuming call inside it is consumed once.
from typing import Self
from tpy import Own, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, x: Own[T]) -> None:
        self.items.append(x)


class K:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def m[U](self, x: Own[U]) -> Own[U]:
        self.n += 1
        return x

    def first[U](self, xs: list[U]) -> U:
        return xs[0]


def main() -> None:
    k = K()
    c = B(1)
    print("call:", k.m(c.take()).v)  # tpyc: ok
    c = B(2)
    print("field:", k.m(c.take().v))  # tpyc: ok
    c = B(3)
    print("list:", k.first([c.take().v]))  # tpyc: ok
    # (a keyword argument does not bind a method's type parameter yet,
    # BUGS.md#generic-method-keyword-arg-not-inferred)
    print("calls:", k.n)
    # a receiver whose type arguments the call infers (a pending instance)
    c = B(4)
    bag = Bag()
    bag.put(c.take())  # tpyc: ok
    print("pending-receiver:", bag.items[0].v)


main()
