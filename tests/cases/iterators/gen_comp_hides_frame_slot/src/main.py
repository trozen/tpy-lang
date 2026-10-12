# A comprehension variable named like an owning frame slot of its generator /
# async function (a local, a with target, a match capture) is its own binding:
# its reads do not deref the slot, and the slot keeps its object.
import asyncio
from typing import Iterator


class Cell:
    def __init__(self, n: int) -> None:
        self.n = n


class Mgr:
    c: Cell

    def __init__(self, n: int) -> None:
        self.c = Cell(n)

    def __enter__(self) -> Cell:
        return self.c

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def gen_local(cells: list[Cell]) -> Iterator[int]:
    # generator local: `c` is a frame slot live across the yields.
    c = Cell(5)
    yield c.n
    ns = [c.n + 1 for c in cells]  # tpyc: ok
    yield ns[0] + ns[1]
    c.n += 10
    cells[0].n += 100
    yield c.n + cells[0].n


def gen_with(m: Mgr, cells: list[Cell]) -> Iterator[int]:
    # generator with target: `c` aliases the manager's cell across the yields.
    with m as c:
        yield c.n
        yield sum([c.n for c in cells])  # tpyc: ok
        c.n += 100
        yield c.n


def gen_match(s: Cell | int, cells: list[Cell]) -> Iterator[int]:
    # generator match capture: `c` is captured by the case.
    match s:
        case Cell() as c:
            yield c.n
            c.n += len([c.n for c in cells])  # tpyc: ok
            yield c.n
        case _:
            yield 0


async def async_local(cells: list[Cell]) -> int:
    # async local: `c` is a frame slot live across the await.
    c = Cell(3)
    await asyncio.sleep(0)
    ns = [c.n * 2 for c in cells]  # tpyc: ok
    c.n += ns[1]
    return c.n


async def async_loop_var(rows: list[list[Cell]]) -> int:
    # async for statement: its variable `cs` is a frame slot the clause hides.
    await asyncio.sleep(0)
    r = [c.n for cs in rows for c in cs]  # tpyc: ok
    t = 0
    for cs in rows:
        for c in cs:
            c.n += 1
            t += c.n
    return sum(r) * 100 + t


def main() -> None:
    cells = [Cell(1), Cell(2)]
    print("gen_local:", list(gen_local(cells)), cells[0].n)
    m = Mgr(7)
    print("gen_with:", list(gen_with(m, cells)), m.c.n)
    print("gen_match:", list(gen_match(Cell(9), cells)))
    print("async_local:", asyncio.run(async_local(cells)))
    rows = [[Cell(1)], [Cell(2), Cell(3)]]
    print("async_loop_var:", asyncio.run(async_loop_var(rows)), rows[1][0].n)


main()
