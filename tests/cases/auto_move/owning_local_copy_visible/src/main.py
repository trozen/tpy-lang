# The warned copy of a non-owning local is its own object: a mutation of the result
# does not reach the caller's (CPython's would -- the divergence the warning declares).
from typing import Self
from tpy import Own, int32


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[Self]:
        return self

    def items(self) -> list[int32]:
        return self.xs


class Pair:
    pair: tuple[Box, Box]

    def __init__(self) -> None:
        self.pair = (Box(7), Box(8))


def make_pair(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


def head(h: Pair) -> int32:
    a, b = h.pair
    return len(a.xs)


# free function: a rebound local at an `Own` return
def ret(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# free function: a consuming method call on a rebound local
def receiver(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return b.finish()  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# free function: a tuple element one binding borrows
def elem(h: Pair, c: bool) -> Own[Box]:
    p = make_pair(1)
    if c:
        p = h.pair
    return p[0]  # tpyc: warning(/copies Box into owned storage.*'p' is bound to existing storage/)


# free function: a consuming call on an owning local a call result still borrows
def recv_lent() -> Own[Box]:
    b = Box(1)
    v = b.items()
    r = b.finish()  # tpyc: warning(/copies Box into owned storage/)
    v.append(5)
    print("recv_lent", len(v), len(r.xs))
    return r


# free function: a repeated literal's element is a list literal element
def repeat(w: Box, c: bool) -> Own[list[Box]]:
    b = w
    if c:
        b = Box(1)
    return [b] * 2  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


def main() -> None:
    w = Box(7)
    r = ret(w, False)
    r.xs.append(9)
    print("ret", w.xs, r.xs)
    w = Box(7)
    r = receiver(w, False)
    r.xs.append(9)
    print("receiver", w.xs, r.xs)
    h = Pair()
    r = elem(h, True)
    r.xs.append(9)
    print("elem", head(h), r.xs)
    recv_lent()
    w = Box(7)
    rp = repeat(w, False)
    rp[0].xs.append(9)
    print("repeat", w.xs, rp[0].xs)


main()
