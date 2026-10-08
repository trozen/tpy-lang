# An element of a tuple the body OWNS -- a `tuple[Own[Box], Own[Box]]` or
# mixed `tuple[Own[Box], Box]` parameter, an `Own[tuple]` parameter, an owned
# local -- is a place like the same element of a borrowed tuple: a name
# bound to it aliases it, a method call (or one through a field of it)
# mutates it in place, and an `Own[T]` slot moves it out at the tuple's last
# use. Each section mutates after the boundary and prints, so a copy shows.
from enum import Enum
from typing import Iterator
from tpy import int32, Own, readonly


class Color(Enum):
    Red = 1
    Blue = 2


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.xs = [1, 2]

    def inc(self) -> None:
        self.n += 1

    @readonly
    def get(self) -> int32:
        return self.n

    def bump(self) -> int32:
        self.n += 1
        return self.n


def sink(keep: list[Box], b: Own[Box]) -> int32:
    keep.append(b)
    return keep[-1].n


def mk(c: Color) -> tuple[Own[Box], Color]:
    return (Box(1), c)


# alias, free function: the owned element binds `Box&`, the borrowed one
# `(*std::get<1>(p))`, so a write through it reaches the caller's object.
def alias_owned(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
    o = p[0]  # tpyc: ok
    o.n += 10
    return p[0].n + p[1].n


def alias_mixed(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/never consumed/)
    o = p[0]  # tpyc: ok
    o.n += 10
    q = p[1]
    q.n += 100
    return p[0].n


# the whole tuple bound to a name aliases it too (`auto&& q = p`): a write
# through `q` is seen through the element alias `o`.
def alias_whole_name(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
    o = p[0]
    q = p  # tpyc: ok
    q[0].n = 99
    return o.n + p[1].n


def alias_whole(p: Own[tuple[Box, int]]) -> int:
    o = p[0]
    o.n += 10
    return p[0].n + p[1]


# alias declared inside a loop body
def alias_loop(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
    total = 0
    for i in range(3):
        o = p[0]  # tpyc: ok
        o.n += i
        total += o.n
    return total + p[0].n


# alias of a readonly param's owned element: the callee owns the moved-in object,
# so the alias is mutable (`Box&`), as a scalar `readonly[Own[Box]]` param is.
def alias_readonly(p: readonly[tuple[Own[Box], int32]]) -> int32:  # tpyc: warning(/never consumed/)
    o = p[0]  # tpyc: ok
    return o.get() + p[1]


# writes through a readonly param's owned element, at the subscript and at
# the unpack target alike: one rule, the scalar's (readonly protects nothing
# the callee owns); a borrowed element beside it stays readonly.
def readonly_owned_write(p: readonly[tuple[Own[Box], int32]]) -> int32:  # tpyc: warning(/never consumed/)
    p[0].bump()  # tpyc: ok
    x, k = p
    x.bump()  # tpyc: ok
    return x.get() + k


# method call on the element, and through a field of it
def method_owned(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
    p[0].inc()  # tpyc: ok
    return p[0].get() + p[1].n


def method_mixed(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/never consumed/)
    p[0].inc()  # tpyc: ok
    p[1].inc()
    return p[0].get()


def field_method(p: tuple[Own[Box], int32]) -> int32:  # tpyc: warning(/never consumed/)
    p[0].xs.pop()  # tpyc: ok
    return len(p[0].xs) + p[1]


def field_method_borrowed(p: tuple[Box, int]) -> int:
    p[0].xs.append(7)  # tpyc: ok
    return len(p[0].xs) + p[1]


# closure body: the method call on the captured param's owned element
def closure_method(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/never consumed/)
    def g() -> None:
        p[0].inc()  # tpyc: ok
    g()
    return p[0].n + p[1].n


# generator expression inside a generator frame
def genexpr_in_gen(p: tuple[Own[Box], Box]) -> Iterator[int32]:  # tpyc: warning(/never consumed/)
    yield 1 if any(p[0].bump() > 0 for _ in range(2)) else 0  # tpyc: ok
    yield p[0].n


# ... and a field-chain method call from a generator expression
def genexpr_field_chain(p: tuple[Own[Box], Box]) -> bool:  # tpyc: warning(/never consumed/)
    return any(p[0].xs.pop() > 0 for _ in range(1))  # tpyc: ok


# the element of an `Own[tuple]` param or an owned local passed to `Own[T]`
# moves at the tuple's last use (`std::move(std::get<0>(p))`)
def pass_whole(keep: list[Box], p: Own[tuple[Box, int32]]) -> int32:
    return sink(keep, p[0])  # tpyc: ok


def pass_local(keep: list[Box]) -> int32:
    p = (Box(5), 2)
    return sink(keep, p[0])  # tpyc: ok


# ... and a live alias of it is a borrower: the slot copies, warned
def pass_live_alias(keep: list[Box], p: Own[tuple[Box, int32]]) -> int32:
    o = p[0]
    r = sink(keep, p[0])  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return r + o.n - o.n


# return of the owned element into `-> Own[Box]` moves it out
def ret_whole(p: Own[tuple[Box, int32]]) -> Own[Box]:
    o = p[0]
    o.n += 1
    return p[0]  # tpyc: ok


def ret_local() -> Own[Box]:
    p = (Box(8), 2)
    return p[0]  # tpyc: ok


# unpack: an enum element binds by value beside an owned or borrowed one
def unpack_call() -> None:
    x, c = mk(Color.Blue)  # tpyc: ok
    x.n += 5
    print("unpack_call", x.n, c == Color.Blue)


def unpack_borrowed(p: tuple[Box, Color]) -> None:
    x, c = p  # tpyc: ok
    x.n += 5
    print("unpack_borrowed", x.n, c == Color.Red)


class H:
    k: int32

    def __init__(self) -> None:
        self.k = 3

    # the method twins: `self` beside the tuple parameter
    def alias_mixed(self, p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/never consumed/)
        o = p[0]  # tpyc: ok
        o.n += self.k
        q = p[1]
        q.n += 100
        return p[0].n

    def alias_owned(self, p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
        o = p[0]  # tpyc: ok
        o.n += self.k
        return p[0].n + p[1].n

    def method_owned(self, p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
        p[0].inc()  # tpyc: ok
        return p[0].get() + p[1].n + self.k

    def method_mixed(self, p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/never consumed/)
        p[0].inc()  # tpyc: ok
        p[1].inc()
        return p[0].get() + self.k

    def field_method(self, p: tuple[Own[Box], int32]) -> int32:  # tpyc: warning(/never consumed/)
        p[0].xs.pop()  # tpyc: ok
        return len(p[0].xs) + p[1] + self.k


def main() -> None:
    b = Box(2)
    print("alias_owned", alias_owned((Box(1), Box(2))))
    print("alias_mixed", alias_mixed((Box(1), b)), b.n)
    print("alias_whole_name", alias_whole_name((Box(1), Box(2))))
    print("alias_whole", alias_whole((Box(1), 2)))
    print("alias_loop", alias_loop((Box(1), Box(2))))
    print("alias_readonly", alias_readonly((Box(5), 2)))
    print("readonly_owned_write", readonly_owned_write((Box(5), 2)))
    print("method_owned", method_owned((Box(1), Box(2))))
    print("method_mixed", method_mixed((Box(1), b)), b.n)
    print("field_method", field_method((Box(1), 2)))
    fb = Box(1)
    print("field_method_borrowed", field_method_borrowed((fb, 2)), len(fb.xs))
    print("closure_method", closure_method((Box(1), b)))
    print("genexpr_in_gen", list(genexpr_in_gen((Box(1), b))))
    print("genexpr_field_chain", genexpr_field_chain((Box(1), b)))
    keep: list[Box] = []
    print("pass_whole", pass_whole(keep, (Box(5), 2)))
    print("pass_local", pass_local(keep))
    keep[0].n += 1000
    print("pass_kept", keep[0].n, keep[1].n)
    print("pass_live_alias", pass_live_alias(keep, (Box(6), 2)))
    r = ret_whole((Box(7), 2))
    r.n += 500
    print("ret_whole", r.n)
    print("ret_local", ret_local().n)
    unpack_call()
    ub = Box(1)
    unpack_borrowed((ub, Color.Red))
    print("unpack_borrowed_caller", ub.n)
    h = H()
    print("m_alias_mixed", h.alias_mixed((Box(1), b)), b.n)
    print("m_alias_owned", h.alias_owned((Box(1), Box(2))))
    print("m_method_owned", h.method_owned((Box(1), Box(2))))
    print("m_method_mixed", h.method_mixed((Box(1), b)), b.n)
    print("m_field_method", h.field_method((Box(1), 2)))


main()
