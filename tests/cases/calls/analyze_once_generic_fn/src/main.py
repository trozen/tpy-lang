# A generic function's argument is analyzed once: inference reads the
# argument's one analysis and the commit receives it, so a consuming call
# inside it is consumed once, under each carrier.
from typing import Self
from tpy import Own, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


def g[T](x: Own[T]) -> Own[T]:
    return x


def first[T](xs: list[T]) -> T:
    return xs[0]


def ident[T](x: T) -> T:
    return x


def bump[T](x: T, b: B) -> T:
    # the parameter aliases the caller's object: the write shows there
    b.v += 100
    return x


def main() -> None:
    flag = True
    c = B(1)
    print("call:", g(c.take()).v)  # tpyc: ok
    c = B(2)
    print("field:", g(c.take().v))  # tpyc: ok
    c = B(3)
    print("binop:", g(c.take().v + 1))  # tpyc: ok
    c = B(4)
    print("select-arm:", g(c.take().v if flag else 0))  # tpyc: ok
    # (a walrus and a dict literal at a generic slot are gaps of their own,
    # BUGS.md#walrus-at-generic-own-slot-cpp-error and
    # BUGS.md#dict-set-literal-at-generic-slot-rejects: the @dispatch case
    # holds those sections)
    c = B(6)
    print("fstring:", g(f"{c.take().v}"))  # tpyc: ok
    c = B(7)
    print("list:", first([c.take().v]))  # tpyc: ok
    c = B(9)
    print("tuple:", g((c.take().v, "s"))[0])  # tpyc: ok
    c = B(10)
    print("nested-generic:", g(g(c.take())).v)  # tpyc: ok
    a = B(11)
    print("name:", bump(1, a), a.v)
    # a static protocol slot (len's Sized) bounds the inner call's type
    # parameter, it does not bind it
    xs = [1, 2, 3]
    print("protocol-slot:", len(ident(xs)))  # tpyc: ok
    r1: list[int] = [1]
    r2: list[int] = [1, 2]
    print("protocol-slot-key:", len(max(r1, r2, key=lambda r: len(r))))  # tpyc: ok
    # the key variant of max is a plain stub in a builtin overload set: its
    # slot step takes the set's route, which settles the str locals
    s1 = "ab"
    s2 = "abc"
    print("protocol-slot-str:", len(max(s1, s2, key=lambda r: len(r))))  # tpyc: ok


main()
