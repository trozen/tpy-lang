# A `with` nested in each kind of compound statement, its target read after
# the outer statement: the manager is kept where the target is declared, and
# rebuilt from the previous pass's target before that one is destroyed.
import asyncio
from typing import Iterator, Self
from tpy import Array, int32, nocopy


class Mgr:
    name: str
    n: int32

    def __init__(self, name: str, n: int32) -> None:
        self.name = name
        self.n = n

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


def seed() -> str:
    return "seed-name-long-enough-to-heap-" * 2


def gen() -> Iterator[int32]:
    yield 1
    yield 2


def churn() -> None:
    junk = [seed() + str(k) for k in range(20)]
    print("compound churn", len(junk))


def kinds(c: bool) -> None:
    m = Mgr(seed(), 0)
    # for, plain and with a head temporary: each pass builds from the last
    for i in range(2):
        with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
            pass
    churn()
    print("compound for", m.n, m.name == seed())
    for x in gen():
        with Mgr(m.name, m.n + x) as m:  # tpyc: ok
            pass
    churn()
    print("compound for head", m.n, m.name == seed())
    k = 0
    while k < 2:
        with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
            pass
        k += 1
    churn()
    print("compound while", m.n, m.name == seed())
    if c:
        with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
            pass
    churn()
    print("compound if", m.n, m.name == seed())
    try:
        with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
            pass
    finally:
        print("compound finally", m.n)
    churn()
    print("compound try", m.n, m.name == seed())
    with Mgr(m.name, 100) as outer:  # tpyc: ok
        with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
            pass
    churn()
    print("compound with", m.n, outer.n, m.name == seed())
    match k:
        case 2:
            with Mgr(m.name, m.n + 1) as m:  # tpyc: ok
                pass
        case _:
            pass
    churn()
    print("compound match", m.n, m.name == seed())


class Copying:
    t: list[int32]

    def __init__(self, src: list[int32]) -> None:
        self.t = src.copy()
        self.t.append(len(src))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass

    def __del__(self) -> None:
        self.t.clear()


def rebuilt() -> None:
    # the constructor reads the previous pass's target: it is built first
    t = Copying([1, 2])
    for i in range(3):
        with Copying(t.t) as t:  # tpyc: ok
            pass
        print("compound rebuilt", t.t)


class Grow:
    t: list[int32]

    def __init__(self, src: list[int32]) -> None:
        self.t = src.copy()
        self.t.append(len(src))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


def gen_rebuilt() -> Iterator[int32]:
    # the same in a generator and a coroutine: the frame holds the manager
    t = Grow([1, 2])
    for i in range(3):
        with Grow(t.t) as t:  # tpyc: ok
            yield len(t.t)
    churn()
    yield len(t.t)


async def co_rebuilt() -> int32:
    t = Grow([1, 2])
    for i in range(3):
        with Grow(t.t) as t:  # tpyc: ok
            await asyncio.sleep(0)
    churn()
    return len(t.t)


@nocopy
class Pinned:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


class Grid:
    cells: Array[int32, 4]

    def __init__(self, n: int32) -> None:
        self.cells = Array[int32, 4]()
        self.cells[3] = n

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


def in_place(c: bool) -> None:
    # built where it is kept: a manager that does not copy, and one whose
    # move copies its array
    p = Pinned(0)
    # an alias of the first manager keeps it, whatever the target rebinds to
    alias = p
    g = Grid(0)
    if c:
        with Pinned(5) as p:  # tpyc: ok
            pass
        with Grid(7) as g:  # tpyc: ok
            pass
    churn()
    print("compound in place", p.n, alias.n, g.cells[3])


def main() -> None:
    kinds(True)
    kinds(False)
    rebuilt()
    gr = list(gen_rebuilt())
    print("compound gen rebuilt", gr)
    cr = asyncio.run(co_rebuilt())
    print("compound co rebuilt", cr)
    in_place(True)
    in_place(False)
