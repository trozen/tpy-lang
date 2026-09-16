# A resumable frame reads the Phase-2 const-borrow verdict for its reference
# parameters, so a read-only container param is captured `const T&` -- the
# spelling the non-yielding twin of the same body already infers -- and a
# caller holding its own borrow can drive the generator. A mutated param, and
# one forwarded to a mutating callee, keep the mutable borrow. The `.items()`
# / `.values()` frame slots and a `match` capture follow the same verdict.
from typing import Iterator, Optional
from tpy import int32
import asyncio


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box:
    inner: Optional[Rec]

    def __init__(self, n: int32, has: bool) -> None:
        self.inner = None
        if has:
            self.inner = Rec(n)


class Bag:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    # method generator -- the verdict is looked up on the record's own
    # method, not on a same-named free function
    def scan(self, ys: list[int32]) -> Iterator[int32]:  # tpyc: ok
        for y in ys:
            yield y + self.base


# free generator, read-only container param
def ro(xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
    for x in xs:
        yield x


# the read-only caller: it holds its own borrow and passes it straight on
def drive_ro(xs: list[int32]) -> int32:
    t = 0
    for v in ro(xs):
        t += v
    return t


# mutated param -- stays `T&`, and the yielded element aliases the caller's
# object, so the mutation after the yield boundary is visible in `recs`
def mut(recs: list[Rec]) -> Iterator[Rec]:  # tpyc: ok
    recs.append(Rec(4))
    for r in recs:
        yield r


def bump(xs: list[int32]) -> None:
    xs.append(9)


# param forwarded to a mutating callee -- stays `T&`
def to_mut_callee(xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
    bump(xs)
    for x in xs:
        yield x


# `.items()` view over a read-only dict param, read across a yield
def items_ro(d: dict[int32, int32]) -> Iterator[int32]:  # tpyc: ok
    for k, v in d.items():
        yield k
        yield v


# `.values()` sibling of the same shape
def values_ro(d: dict[int32, int32]) -> Iterator[int32]:  # tpyc: ok
    for v in d.values():
        yield v


def drive_views(d: dict[int32, int32]) -> int32:
    t = 0
    for a in items_ro(d):
        t += a
    for b in values_ro(d):
        t += b
    return t


# `match` binding off a read-only subject param, read after a yield
def match_ro(b: Box) -> Iterator[int32]:  # tpyc: ok
    match b:
        case Box(inner=v):
            yield 1
            if v is not None:
                yield v.n


def drive_match(b: Box) -> int32:
    t = 0
    for v in match_ro(b):
        t += v
    return t


# async twin of `ro`
async def aro(xs: list[int32]) -> int32:  # tpyc: ok
    t = 0
    for x in xs:
        await asyncio.sleep(0)
        t += x
    return t


async def drive_aro(xs: list[int32]) -> int32:
    return await aro(xs)


def drive_scan(g: Bag, ys: list[int32]) -> int32:
    t = 0
    for v in g.scan(ys):
        t += v
    return t


def main() -> None:
    xs = [1, 2, 3]
    print("ro", drive_ro(xs))

    recs = [Rec(1), Rec(2)]
    for r in mut(recs):
        r.n += 10
    print("mut", recs[0].n, recs[1].n, recs[2].n)

    ys = [5, 6]
    for v in to_mut_callee(ys):
        print("callee", v)

    d = {1: 10, 2: 20}
    print("views", drive_views(d))

    print("match", drive_match(Box(7, True)))
    print("match none", drive_match(Box(7, False)))

    print("async", asyncio.run(drive_aro(xs)))
    print("method", drive_scan(Bag(100), xs))


main()
