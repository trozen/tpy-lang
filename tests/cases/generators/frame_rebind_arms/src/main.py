# A rebound reference local of a generator / async frame (a `T*` over storage
# of its own per binding site) at the yield ternary, print, walrus, unpack and
# with-as arms (an unpack that is the sole rebind included, an `__enter__` that
# owns, lends, lends readonly or is generic, a rebind to a parameter, a with
# target over a local bound earlier); a loop variable at the same arms rides
# the same frame-storage check, and a rebound with / unpack target reads as a
# compare operand.
import asyncio
from typing import Iterator

from tpy import Own, int32, readonly


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


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> Own[P]:
        return P(1)

    def __exit__(self, kind, value, tb) -> None:
        pass


# with-as: the target's first object lands in a frame field of the item's
# own, so the alias keeps it across a rebind, by rvalue or by lvalue
def with_rvalue_gen() -> Iterator[int32]:
    with Ctx() as p:
        q = p
        yield 0
        p = P(2)  # tpyc: ok
        q.n += 10
        print("with_rvalue gen:", q.n, p.n)
        yield 1


def with_lvalue_gen() -> Iterator[int32]:
    other = P(5)
    with Ctx() as p:
        q = p
        yield 0
        p = other  # tpyc: ok
        p.n += 100
        print("with_lvalue gen:", q.n, p.n, other.n)
        yield 1


async def with_rvalue_co() -> int32:
    with Ctx() as p:
        q = p
        await asyncio.sleep(0)
        p = P(2)  # tpyc: ok
        q.n += 10
        print("with_rvalue co:", q.n, p.n)
    return p.n


# with-as in a with body that does not suspend (the leaf with)
def with_leaf_gen() -> Iterator[int32]:
    with Ctx() as p:
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
    yield 0
    print("with_leaf gen:", q.n, p.n)
    yield 1


class LentCtx:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def __enter__(self) -> P:
        return self.p

    def __exit__(self, kind, value, tb) -> None:
        pass


# with-as over an `__enter__` that lends: the target points at the manager's
# object, a rebind after a suspension re-points it
def with_lent_gen(c: LentCtx) -> Iterator[int32]:
    with c as p:
        q = p
        yield 0
        p = P(2)  # tpyc: ok
        q.n += 10
        print("with_lent gen:", q.n, p.n, c.p.n)
        yield 1


async def with_lent_co(c: LentCtx) -> int32:
    with c as p:
        q = p
        await asyncio.sleep(0)
        p = P(2)  # tpyc: ok
        q.n += 10
        print("with_lent co:", q.n, p.n, c.p.n)
    return p.n


# with-as over a lending `__enter__`, rebound to a parameter: the target
# re-points at the caller's object, the alias keeps the manager's
def with_lent_param_gen(c: LentCtx, o: P) -> Iterator[int32]:
    with c as p:
        q = p
        yield 0
        p = o  # tpyc: ok
        p.n += 100
        q.n += 10
        print("with_lent_param gen:", q.n, p.n, o.n, c.p.n)
        yield 1


async def with_lent_param_co(c: LentCtx, o: P) -> int32:
    with c as p:
        q = p
        await asyncio.sleep(0)
        p = o  # tpyc: ok
        p.n += 100
        q.n += 10
        print("with_lent_param co:", q.n, p.n, o.n, c.p.n)
    return p.n


class RoCtx:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def __enter__(self) -> readonly[P]:
        return self.p

    def __exit__(self, kind, value, tb) -> None:
        self.p.n += 10


# with-as over an `__enter__` that lends a readonly object, rebound to an
# existing one: a const pointer the rebind re-points; writes to the objects
# themselves show through both names
def with_readonly_gen(c: RoCtx) -> Iterator[int32]:
    other = P(7)
    with c as p:
        q = p
        yield 0
        p = other  # tpyc: ok
        other.n += 100
        c.p.n += 10
        print("with_readonly gen:", q.n, p.n, c.p.n)
        yield 1


async def with_readonly_co(c: RoCtx) -> int32:
    other = P(8)
    with c as p:
        q = p
        await asyncio.sleep(0)
        p = other  # tpyc: ok
        other.n += 100
        c.p.n += 10
        print("with_readonly co:", q.n, p.n, c.p.n)
    return p.n


# with-as over a readonly `__enter__` in a with body that does not suspend:
# the const pointer re-points at an existing object, the alias keeps the
# manager's, so it sees the write `__exit__` makes to it
def with_readonly_leaf_gen() -> Iterator[int32]:
    other = P(7)
    yield 0
    with RoCtx() as p:
        q = p
        p = other  # tpyc: ok
        other.n += 100
    print("with_readonly_leaf gen:", q.n, p.n)
    yield 1


class GCtx[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def __enter__(self) -> T:
        return self.v

    def __exit__(self, kind, value, tb) -> None:
        pass


# with-as over a generic `__enter__ -> T`: the target lends the manager's
# object, a rebind re-points it and the alias keeps the manager's
def with_generic_gen(c: GCtx[P], o: P) -> Iterator[int32]:
    with c as p:
        q = p
        yield 0
        p = o  # tpyc: ok
        p.n += 100
        q.n += 10
        print("with_generic gen:", q.n, p.n, o.n, c.v.n)
        yield 1


def with_generic_rvalue_gen(c: GCtx[P]) -> Iterator[int32]:
    with c as p:
        q = p
        yield 0
        p = P(5)  # tpyc: ok
        q.n += 10
        print("with_generic_rvalue gen:", q.n, p.n, c.v.n)
        yield 1


async def with_generic_co(c: GCtx[P], o: P) -> int32:
    with c as p:
        q = p
        await asyncio.sleep(0)
        p = o  # tpyc: ok
        p.n += 100
        q.n += 10
        print("with_generic co:", q.n, p.n, o.n, c.v.n)
    return p.n


class Res:
    n: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = [n]

    def __enter__(self) -> "Res":
        return self

    def __exit__(self, kind, value, tb) -> None:
        self.n[0] = -self.n[0]


# with-as whose target is a local bound earlier in the frame: the target
# takes the manager's object, whose `__exit__` the name then shows
def with_target_bound_gen() -> Iterator[int32]:
    t = Res(1)
    keep = t
    yield 0
    with Res(6) as t:  # tpyc: ok
        pass
    print("with_target_bound gen:", t.n[0], keep.n[0])
    yield 1


async def with_target_bound_co() -> int32:
    t = Res(1)
    keep = t
    await asyncio.sleep(0)
    with Res(6) as t:  # tpyc: ok
        pass
    print("with_target_bound co:", t.n[0], keep.n[0])
    return t.n[0]


# the same, over a local rebound before the with
def with_target_rebound_gen() -> Iterator[int32]:
    p = Res(1)
    yield 0
    p = Res(2)
    keep = p
    with Res(3) as p:  # tpyc: ok
        pass
    print("with_target_rebound gen:", p.n[0], keep.n[0])
    yield 1


async def with_target_rebound_co() -> int32:
    p = Res(1)
    await asyncio.sleep(0)
    p = Res(2)
    keep = p
    with Res(3) as p:  # tpyc: ok
        pass
    print("with_target_rebound co:", p.n[0], keep.n[0])
    return p.n[0]


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


class Cl:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, o: "Cl") -> bool:
        return self.n < o.n


class ClCtx:
    c: Cl

    def __init__(self) -> None:
        self.c = Cl(1)

    def __enter__(self) -> Cl:
        return self.c

    def __exit__(self, kind, value, tb) -> None:
        pass


def mkc(i: int32) -> tuple[Own[Cl], int32]:
    return (Cl(i), i)


# compare: a with target and an unpack target rebound after a suspension,
# then mutated through the rebound object's name, read as compare operands
# (a rebound owning for target is BUGS.md#frame-for-own-target-rebind-rejects)
def compare_gen() -> Iterator[int32]:
    o = Cl(5)
    with ClCtx() as p:
        yield 0
        p = o
        o.n += 1
        print("compare gen:", Cl(5) < p, p < Cl(6), Cl(0) < p < Cl(9))  # tpyc: ok
        yield 1
    q = Cl(1)
    yield 2
    q = Cl(2)
    q, k = mkc(6)
    keep = q
    keep.n += 1
    print("compare gen:", Cl(6) < q, q < Cl(7), Cl(0) < q < Cl(9), k)  # tpyc: ok


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
    for x in with_rvalue_gen():
        print("with_rvalue:", x)
    for x in with_lvalue_gen():
        print("with_lvalue:", x)
    r = asyncio.run(with_rvalue_co())
    print("with_rvalue:", r)
    for x in with_leaf_gen():
        print("with_leaf:", x)
    for x in with_lent_gen(LentCtx()):
        print("with_lent:", x)
    r = asyncio.run(with_lent_co(LentCtx()))
    print("with_lent:", r)
    for x in with_lent_param_gen(LentCtx(), P(3)):
        print("with_lent_param:", x)
    r = asyncio.run(with_lent_param_co(LentCtx(), P(4)))
    print("with_lent_param:", r)
    for x in with_readonly_gen(RoCtx()):
        print("with_readonly:", x)
    r = asyncio.run(with_readonly_co(RoCtx()))
    print("with_readonly:", r)
    for x in with_readonly_leaf_gen():
        print("with_readonly_leaf:", x)
    for x in with_generic_gen(GCtx(P(1)), P(3)):
        print("with_generic:", x)
    for x in with_generic_rvalue_gen(GCtx(P(1))):
        print("with_generic_rvalue:", x)
    r = asyncio.run(with_generic_co(GCtx(P(1)), P(4)))
    print("with_generic:", r)
    for x in with_target_bound_gen():
        print("with_target_bound:", x)
    r = asyncio.run(with_target_bound_co())
    print("with_target_bound:", r)
    for x in with_target_rebound_gen():
        print("with_target_rebound:", x)
    r = asyncio.run(with_target_rebound_co())
    print("with_target_rebound:", r)
    ps = [P(1), P(5)]
    xss = [[1], [2, 3]]
    for c in loop_gen(ps, xss, True):
        c.n += 100
    print("loop:", ps[0].n, ps[1].n, xss)
    for x in compare_gen():
        print("compare:", x)


main()
