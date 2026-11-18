# copy_iter in a generator, a coroutine, a closure and a method; a tuple
# unpack head across a suspension holds owned str / bytes elements.
import asyncio
from typing import Iterator
from tpy import int32, Own, copy_iter
from fixtures import Cell, take


def gen_cells(cells: list[Cell]) -> Iterator[int32]:
    # generator body: each step a copy across the yield
    for c in copy_iter(cells):  # tpyc: ok
        c.v += 1
        yield c.v


def gen_pairs(qs: list[tuple[Cell, int32]]) -> Iterator[int32]:
    # tuple unpack head over copy_iter in a generator frame
    for c, j in copy_iter(qs):  # tpyc: ok
        yield j
        c.v += 1
        yield c.v


def gen_strs(qs: list[tuple[str, int32]]) -> Iterator[str]:
    # an owned str element lives in the frame across the yield
    for s, j in copy_iter(qs):  # tpyc: ok
        yield s


def gen_bytes(qs: list[tuple[bytes, int32]]) -> Iterator[bytes]:
    # the bytes twin
    for b, j in copy_iter(qs):  # tpyc: ok
        yield b


def gen_str_loop(ss: list[str]) -> Iterator[int32]:
    # a scalar str loop variable over copy_iter owns its copy across the
    # yield (an owned `std::string` frame field, moved in)
    for s in copy_iter(ss):  # tpyc: ok
        yield len(s)
        print("gen str loop", s)


def gen_bytes_loop(bs: list[bytes]) -> Iterator[int32]:
    # the bytes twin
    for b in copy_iter(bs):  # tpyc: ok
        yield len(b)
        print("gen bytes loop", b)


async def coro_str_loop(ss: list[str]) -> int32:
    # the async twin: a str copy held across an await
    n = 0
    for s in copy_iter(ss):  # tpyc: ok
        await asyncio.sleep(0)
        n += len(s)
        print("async str loop", s)
    return n


async def coro_pairs(qs: list[tuple[Cell, int32]]) -> int32:
    # async tuple-unpack head over copy_iter: the Cell is a copy
    t = 0
    for c, j in copy_iter(qs):  # tpyc: ok
        await asyncio.sleep(0)
        c.v += 1
        t += j + c.v
    return t


async def coro_sum(cells: list[Cell]) -> int32:
    # async body: a copy held across an await
    t = 0
    for c in copy_iter(cells):  # tpyc: ok
        await asyncio.sleep(0)
        c.v += 1
        t += c.v
    return t


class Holder:
    cells: list[Cell]

    def __init__(self) -> None:
        self.cells = [Cell(3), Cell(4)]

    def copies(self) -> Own[list[Cell]]:
        # method: copy_iter over a field
        return take(copy_iter(self.cells))  # tpyc: ok


def main() -> None:
    cells = [Cell(1), Cell(2)]
    print("generator", list(gen_cells(cells)), cells[0].v)
    qs = [(Cell(5), 6)]
    print("gen unpack", list(gen_pairs(qs)), qs[0][0].v)
    print("gen str", list(gen_strs([("ab" + str(1), 1), ("cd", 2)])))
    print("gen bytes", list(gen_bytes([(b"xy", 1)])))
    print("async", asyncio.run(coro_sum(cells)), cells[1].v)
    ss = ["p", "qr"]
    lens = list(gen_str_loop(ss))
    ss[0] = "changed"
    print("gen str after", lens, ss)
    bs = [b"xyz"]
    blens = list(gen_bytes_loop(bs))
    bs[0] = b"w"
    print("gen bytes after", blens, bs)
    total_len = asyncio.run(coro_str_loop(ss))
    ss[1] = "s"
    print("async str after", total_len, ss)
    pq = [(Cell(4), 5)]
    pt = asyncio.run(coro_pairs(pq))
    print("async unpack", pt, pq[0][0].v)
    # closure: a nested def calls copy_iter over a captured list
    def grab() -> Own[list[Cell]]:
        return take(copy_iter(cells))  # tpyc: ok

    got = grab()
    got[0].v = 70
    print("closure", got[0].v, cells[0].v)
    h = Holder()
    hc = h.copies()
    hc[0].v = 30
    print("method", hc[0].v, h.cells[0].v)
