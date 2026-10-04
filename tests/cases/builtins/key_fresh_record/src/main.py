# A fresh reference-type key (ctor lambda, class name, Own helper) at sorted and min/max.
# Sections only read the results; write-through is pinned by builtins/borrow_result.
from __future__ import annotations
import asyncio
from typing import Iterator
from tpy import int32, Own


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def score(self) -> int32:
        return -self.n

    def __lt__(self, o: Rec) -> bool:
        return self.n < o.n


class Key:
    k: int32

    def __init__(self, r: Rec) -> None:
        self.k = -r.n

    def __lt__(self, o: Key) -> bool:
        return self.k < o.k


def make_key(r: Rec) -> Own[Key]:
    return Key(r)


def helper(r: Rec) -> int32:
    return r.n * 2


def ns(rs: list[Rec]) -> Own[list[int32]]:
    return [r.n for r in rs]


def free(rs: list[Rec]) -> None:
    a = rs[0]
    b = rs[1]
    c = rs[2]
    # the constructor lambda returns the fresh Key by value
    print("free sorted_lam", ns(sorted(rs, key=lambda r: Key(r))))  # tpyc: ok
    # the class name is the same lambda, with const params
    print("free sorted_cls", ns(sorted(rs, key=Key)))  # tpyc: ok
    print("free sorted_own", ns(sorted(rs, key=lambda r: make_key(r))))  # tpyc: ok
    print("free min_lam", min(a, b, key=lambda r: Key(r)).n)  # tpyc: ok
    print("free min_cls", min(a, b, key=Key).n)  # tpyc: ok
    print("free max_lam", max(a, b, key=lambda r: Key(r)).n)  # tpyc: ok
    print("free max_cls", max(a, b, key=Key).n)  # tpyc: ok
    print("free min3_lam", min(a, b, c, key=lambda r: Key(r)).n)  # tpyc: ok
    print("free min3_cls", min(a, b, c, key=Key).n)  # tpyc: ok
    print("free max3_own", max(a, b, c, key=lambda r: make_key(r)).n)  # tpyc: ok
    print("free max3_cls", max(a, b, c, key=Key).n)  # tpyc: ok
    # guards: an identity key and non-mutating calls on the const param
    print("free sorted_id", ns(sorted(rs, key=lambda r: r)))  # tpyc: ok
    print("free min_id", min(a, b, key=lambda r: r).n)  # tpyc: ok
    print("free min_method", min(a, b, key=lambda r: r.score()).n)  # tpyc: ok
    print("free max_helper", max(a, b, key=lambda r: helper(r)).n)  # tpyc: ok


class Holder:
    rs: list[Rec]

    def __init__(self) -> None:
        self.rs = [Rec(1), Rec(3), Rec(2)]

    def show(self) -> None:
        a = self.rs[0]
        b = self.rs[1]
        # method: fresh key over a field and over locals
        print("method sorted_lam", ns(sorted(self.rs, key=lambda r: Key(r))))  # tpyc: ok
        print("method sorted_cls", ns(sorted(self.rs, key=Key)))  # tpyc: ok
        print("method min_cls", min(a, b, key=Key).n)  # tpyc: ok


def gen(rs: list[Rec]) -> Iterator[int32]:
    # generator
    for x in sorted(rs, key=lambda q: Key(q)):  # tpyc: ok
        yield x.n
    for y in sorted(rs, key=Key):  # tpyc: ok
        yield y.n


async def co(rs: list[Rec]) -> int32:
    a = rs[0]
    b = rs[1]
    # async: the class-name key and the ctor lambda in a coroutine body
    s = sorted(rs, key=Key)  # tpyc: ok
    t = sorted(rs, key=lambda r: Key(r))  # tpyc: ok
    m = min(a, b, key=Key)  # tpyc: ok
    return s[0].n * 100 + t[1].n * 10 + m.n


def comp(rss: list[list[Rec]]) -> None:
    # comprehension: the key lambda inside the element expression
    print("comp lam", [sorted(rs, key=lambda r: Key(r))[0].n for rs in rss])  # tpyc: ok
    print("comp cls", [sorted(rs, key=Key)[0].n for rs in rss])  # tpyc: ok


# module level: sorted into globals
g_rs = [Rec(1), Rec(3), Rec(2)]
g_lam = sorted(g_rs, key=lambda r: Key(r))  # tpyc: ok
g_cls = sorted(g_rs, key=Key)  # tpyc: ok


def main() -> None:
    free([Rec(1), Rec(3), Rec(2)])
    Holder().show()
    print("gen", [n for n in gen([Rec(1), Rec(3), Rec(2)])])
    print("async", asyncio.run(co([Rec(1), Rec(3), Rec(2)])))
    comp([[Rec(1), Rec(3)], [Rec(5), Rec(4)]])
    print("module lam", ns(g_lam))
    print("module cls", ns(g_cls))


main()
