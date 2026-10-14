# A rebound reference local of a generator / async frame (a `T*` over storage
# of its own per binding site) at the yield ternary, print, walrus and unpack
# arms (an unpack that is the sole rebind included); a loop variable at the
# same arms rides the same frame-storage check.
import asyncio
from typing import Iterator

from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(i: int32) -> tuple[Own[P], int32]:
    return (P(i), i)


# ternary: the consumer mutates the picked object, the generator sees it
def ternary_gen(flag: bool) -> Iterator[P]:
    a = P(1)
    b = P(2)
    yield b
    a = P(3)
    yield a if flag else b  # tpyc: ok
    print("ternary gen:", a.n, b.n)


# print: a rebound list and dict
def print_gen() -> Iterator[int32]:
    xs = [1, 2]
    yield 0
    xs = [3, 4]
    xs.append(5)
    print("print gen:", xs)  # tpyc: ok
    d = {1: 2}
    yield 1
    d = {3: 4}
    print("print gen:", d)  # tpyc: ok


async def print_co() -> int32:
    xs = [1, 2]
    await asyncio.sleep(0)
    xs = [3, 4]
    print("print co:", xs)  # tpyc: ok
    return len(xs)


# walrus alias: a write through the alias reaches the rebound local
def walrus_alias_gen() -> Iterator[int32]:
    xs = [1]
    yield len(xs)
    xs = [1, 2]
    if len(ys := xs) > 1:  # tpyc: ok
        ys.append(9)
        print("walrus_alias gen:", xs)
        yield len(xs)


async def walrus_alias_co() -> int32:
    xs = [1]
    await asyncio.sleep(0)
    xs = [1, 2]
    if len(ys := xs) > 1:  # tpyc: ok
        ys.append(9)
        print("walrus_alias co:", xs)
    return len(xs)


# unpack: the owned element takes storage of its own, an earlier alias keeps
# the object it was given
def unpack_gen() -> Iterator[int32]:
    p = P(1)
    yield p.n
    p = P(2)
    old = p
    p, k = mk(3)  # tpyc: ok
    p.n += 10
    print("unpack gen:", old.n, p.n, k)
    yield p.n


async def unpack_co() -> int32:
    p = P(1)
    await asyncio.sleep(0)
    p = P(2)
    old = p
    p, k = mk(3)  # tpyc: ok
    p.n += 10
    print("unpack co:", old.n, p.n, k)
    return p.n


# unpack only: the unpack is the local's sole rebind, so it is what makes the
# local pointer-form; the alias keeps the object it was given
def mk2(i: int32) -> tuple[Own[P], Own[P]]:
    return (P(i), P(i + 1))


def unpack_only_gen() -> Iterator[int32]:
    a = P(1)
    keep = a
    yield a.n
    a, b = mk2(10)  # tpyc: ok
    a.n += 5
    print("unpack_only gen:", keep.n, a.n, b.n)
    yield a.n


async def unpack_only_co() -> int32:
    a = P(1)
    keep = a
    await asyncio.sleep(0)
    a, b = mk2(10)  # tpyc: ok
    a.n += 5
    print("unpack_only co:", keep.n, a.n, b.n)
    return a.n


# unpack only, in a loop body: every pass rebinds the local through the unpack
def unpack_only_loop_gen() -> Iterator[int32]:
    a = P(1)
    keep = a
    yield a.n
    for i in range(2):
        a, b = mk2(i * 10)  # tpyc: ok
        yield a.n + b.n
    a.n += 5
    print("unpack_only_loop gen:", keep.n, a.n)


async def unpack_only_loop_co() -> int32:
    a = P(1)
    keep = a
    await asyncio.sleep(0)
    for i in range(2):
        a, b = mk2(i * 10)  # tpyc: ok
        await asyncio.sleep(0)
    a.n += 5
    print("unpack_only_loop co:", keep.n, a.n)
    return a.n


# walrus first binding, rebound later: the alias keeps the walrus's object
def walrus_first_gen() -> Iterator[int32]:
    if len(zs := [5, 6, 7]) > 2:  # tpyc: ok
        yield len(zs)
    ys = zs
    yield 0
    zs = [1, 2]
    zs.append(3)
    print("walrus_first gen:", ys, zs)
    yield len(zs)


async def walrus_first_co() -> int32:
    n = 0
    if len(zs := [5, 6, 7]) > 2:  # tpyc: ok
        n = len(zs)
    ys = zs
    await asyncio.sleep(0)
    zs = [1, 2]
    print("walrus_first co:", ys, zs)
    return n + len(zs)


# loop: a pointer-form loop variable at the ternary, print and walrus arms
def loop_gen(ps: list[P], xss: list[list[int32]], flag: bool) -> Iterator[P]:
    b = P(2)
    for c in ps:
        yield b
        yield c if flag else b  # tpyc: ok
    for xs in xss:
        print("loop gen:", xs)  # tpyc: ok
        if len(ys := xs) > 0:  # tpyc: ok
            ys.append(9)
        yield b


def main() -> None:
    for c in ternary_gen(True):
        c.n += 10
        print("ternary:", c.n)
    for c in ternary_gen(False):
        print("ternary:", c.n)
    for x in print_gen():
        print("print:", x)
    r = asyncio.run(print_co())
    print("print:", r)
    for x in walrus_alias_gen():
        print("walrus_alias:", x)
    r = asyncio.run(walrus_alias_co())
    print("walrus_alias:", r)
    for x in unpack_gen():
        print("unpack:", x)
    r = asyncio.run(unpack_co())
    print("unpack:", r)
    for x in unpack_only_gen():
        print("unpack_only:", x)
    r = asyncio.run(unpack_only_co())
    print("unpack_only:", r)
    for x in unpack_only_loop_gen():
        print("unpack_only_loop:", x)
    r = asyncio.run(unpack_only_loop_co())
    print("unpack_only_loop:", r)
    for x in walrus_first_gen():
        print("walrus_first:", x)
    r = asyncio.run(walrus_first_co())
    print("walrus_first:", r)
    ps = [P(1), P(5)]
    xss = [[1], [2, 3]]
    for c in loop_gen(ps, xss, True):
        c.n += 100
    print("loop:", ps[0].n, ps[1].n, xss)


main()
