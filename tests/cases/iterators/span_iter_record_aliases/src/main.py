# A loop variable over a SpanIter of records (ArrayList's `__iter__`, a user
# `__iter__` returning one) is the element itself, not a copy: every section
# writes through it and reads the container back. Value elements stay copies.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, int32, readonly, SpanIter
from tplib.array_list import ArrayList


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    al: ArrayList[A, 4]

    def __init__(self) -> None:
        self.al = ArrayList[A, 4]()
        self.al.append(A(0))

    def __iter__(self) -> SpanIter[A]:
        return self.al.__iter__()

    def bump_mine(self) -> None:
        for x in self.al:
            x.n += 1  # tpyc: ok


def make() -> Own[ArrayList[A, 4]]:
    al = ArrayList[A, 4]()
    al.append(A(0))
    al.append(A(10))
    return al


def bump_param(al: ArrayList[A, 4]) -> None:
    for x in al:
        x.n += 1  # tpyc: ok


def bump_iterable(it: Iterable[A]) -> None:
    for x in it:
        x.n += 1  # tpyc: ok


def total(al: readonly[ArrayList[A, 4]]) -> int32:
    s: int32 = 0
    for x in al:
        s += x.n
    return s


def gen(al: ArrayList[A, 4]) -> Iterator[int32]:
    for x in al:
        x.n += 1  # tpyc: ok
        yield x.n


async def co(al: ArrayList[A, 4]) -> int32:
    for x in al:
        x.n += 1  # tpyc: ok
        await asyncio.sleep(0)
    return al[0].n


def main() -> None:
    # free function body, both elements
    al = make()
    for x in al:
        x.n += 1  # tpyc: ok
    print("local", al[0].n, al[1].n)
    # parameter, Iterable protocol parameter
    bump_param(al)
    it = al.__iter__()
    bump_iterable(it)
    print("param", al[0].n)
    # method over a field, and a user __iter__ returning SpanIter
    b = Bag()
    b.bump_mine()
    for e in b:
        e.n += 10  # tpyc: ok
    print("method", b.al[0].n)
    # generator and async bodies
    for v in gen(al):
        print("gen", v)
    print("async", asyncio.run(co(al)))

    # closure body
    def inner() -> None:
        for x in al:
            x.n += 1  # tpyc: ok

    inner()
    print("closure", al[0].n)
    # try/finally body and match arm
    try:
        for x in al:
            x.n += 1  # tpyc: ok
    finally:
        print("finally", al[0].n)
    match al[0].n:
        case 7:
            for x in al:
                x.n += 1  # tpyc: ok
        case _:
            pass
    print("match", al[0].n)
    # a binding kept past the loop is the last element
    kept = A(0)
    for x in al:
        kept = x
    kept.n += 100
    print("kept", al[1].n)
    # readonly reads, and value elements copy
    print("readonly", total(al))
    ints = ArrayList[int32, 4]()
    ints.append(1)
    for k in ints:
        k += 1
    print("ints", ints[0])


main()
# module level
gal = make()
for gx in gal:
    gx.n += 1  # tpyc: ok
print("module", gal[0].n)
