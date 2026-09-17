# A resumable frame reads the Phase-2 const-borrow verdict for its reference
# parameters, so a read-only container param is captured `const T&` -- the
# spelling the non-yielding twin of the same body already infers -- and a
# caller holding its own borrow can drive the generator. A mutated param, and
# one forwarded to a mutating callee, keep the mutable borrow. The `.items()`
# / `.values()` frame slots and a `match` capture follow the same verdict.
# A non-value local bound off a LOOP VAR is the same one verdict one hop on:
# the alias's const spelling is the loop var's, which is the iteration's.
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


class Shelf:
    rows: list[list[Rec]]

    def __init__(self) -> None:
        self.rows = [[Rec(1), Rec(2)]]


class Depot:
    shelf: Shelf

    def __init__(self) -> None:
        self.shelf = Shelf()


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


# the inner iterable is a SUBSCRIPT of the outer loop var -- the write through
# the element still credits the param, so it stays `T&` in the frame too
def sub_hop(grid: list[list[list[Rec]]]) -> Iterator[int32]:  # tpyc: ok
    for rows in grid:
        for r in rows[0]:
            r.n += 1
            yield r.n


# the iterable is a one-hop CHAIN off a param (a field, then a subscript) --
# the frame keeps the mutable borrow, as for a bare-name source. A deeper
# chain (`d.shelf.rows[0]`) rejects: the loan has no key for the invalidation
# check (BUGS.md#iter-borrow-place-needs-hops), so the param is a `Shelf`
def chain_hop(s: Shelf) -> Iterator[int32]:  # tpyc: ok
    for r in s.rows[0]:
        r.n += 1
        yield r.n


# async twin of `chain_hop`: the same chain source across a suspension
async def achain_hop(s: Shelf) -> int32:  # tpyc: ok
    t = 0
    for r in s.rows[0]:
        r.n += 1
        await asyncio.sleep(0)
        t += r.n
    return t


# a non-value local bound off the LOOP VAR inside a frame: the alias's const
# spelling is the loop var's, which is the iteration's -- a read-only body
# leaves the param const, so the alias must be a const borrow
def alias_ro(ds: list[Depot]) -> Iterator[int32]:  # tpyc: ok
    for d in ds:
        sh = d.shelf
        yield sh.rows[0][0].n


# the write leg: the alias is written THROUGH, so the borrow is mutable, the
# param keeps `T&`, and the caller sees the change
def alias_mut(ds: list[Depot]) -> Iterator[int32]:  # tpyc: ok
    for d in ds:
        sh = d.shelf
        sh.rows[0][0].n += 1
        yield sh.rows[0][0].n


# async twin of `alias_mut`: the alias spans a suspension
async def aalias_mut(ds: list[Depot]) -> int32:  # tpyc: ok
    t = 0
    for d in ds:
        sh = d.shelf
        sh.rows[0][0].n += 1
        await asyncio.sleep(0)
        t += sh.rows[0][0].n
    return t


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

    grid = [[[Rec(1)]], [[Rec(2)]]]
    for v in sub_hop(grid):
        print("sub hop", v)
    print("sub hop after", grid[0][0][0].n, grid[1][0][0].n)

    d0 = Depot()
    for v in chain_hop(d0.shelf):
        print("chain hop", v)
    print("chain hop after", d0.shelf.rows[0][0].n, d0.shelf.rows[0][1].n)

    d1 = Depot()
    print("async chain hop", asyncio.run(achain_hop(d1.shelf)),
          d1.shelf.rows[0][0].n, d1.shelf.rows[0][1].n)

    ds: list[Depot] = [Depot()]
    for v in alias_ro(ds):
        print("alias ro", v)
    for v in alias_mut(ds):
        print("alias mut", v)
    print("alias async", asyncio.run(aalias_mut(ds)),
          ds[0].shelf.rows[0][0].n)

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
