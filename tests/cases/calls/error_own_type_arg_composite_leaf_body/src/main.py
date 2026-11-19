# Error: a generic whose BODY alone holds its open `T` inside a tuple or an
# Optional is refused at `T = Own[P]`: BUGS.md#own-type-arg-composite-leaf-refused.
from typing import Callable
from tpy import Fn, Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def body_only[T](x: T) -> int32:
    f: Callable[[tuple[T, T]], int32] = lambda p: 1
    return f((x, x))


def literal_only[T](x: T) -> int32:
    t = (x, 1)
    return t[1]


def ternary[T](x: T, c: bool) -> int32:
    t = x if c else None
    return 1 if t is None else 2


def unread[T](x: T) -> int32:
    o: T | None = x
    return 1


def nested[T](x: T) -> int32:
    def inner(t: T | None) -> int32:
        return 1 if t is None else 2
    return inner(x)


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def pair(self) -> tuple[T, int32]:
        return (self.v, 1)


def mk() -> Own[P]:
    return P(6)


def via_method[T](make: Fn[[], T]) -> int32:
    b = Box[T](make())
    v, n = b.pair()
    return n


def main() -> None:
    # The subject: the signature names no composite; the local's does.
    print(body_only[Own[P]](P(1)))  # tpyc: error(/not supported yet: 'T' appears inside 'Callable\[\[tuple\[T, T\]\], int32\]'/)
    # a tuple literal only
    print(literal_only[Own[P]](P(2)))  # tpyc: error(/'T' appears inside 'tuple\[T, int32\]'/)
    # an inferred Optional local
    print(ternary[Own[P]](P(3), True))  # tpyc: error(/'T' appears inside 'T \| None'/)
    # an annotated local no statement reads
    print(unread[Own[P]](P(4)))  # tpyc: error(/'T' appears inside 'T \| None'/)
    # a nested def's parameter: the enclosing generic's `T`
    print(nested[Own[P]](P(5)))  # tpyc: error(/type argument of 'nested' is not supported yet: 'T' appears inside 'T \| None'/)
    # a method call's result: every type in the body counts
    print(via_method[Own[P]](mk))  # tpyc: error(/type argument of 'via_method' is not supported yet: 'T' appears inside 'tuple\[T, int32\]'/)


main()
