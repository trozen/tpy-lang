# Fresh objects a statement keeps through a field chain: a `with` target read
# after the statement, a `@property` chain in a loop head, a borrow tuple's
# element in a generator head, and a fresh object's field taken over by a
# coroutine's `Own` parameter.
import asyncio
from typing import Iterator
from tpy import Own, int32


class Mgr:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    def __enter__(self) -> list[str]:
        self.log.append("enter-long-enough-string-to-heap")
        return self.log

    def __exit__(self, a, b, c) -> None:
        self.log.append("exit")


class Box:
    m: Mgr

    def __init__(self) -> None:
        self.m = Mgr()

    def guard_m(self) -> Mgr:
        return self.m


class Holder:
    b: Box

    def __init__(self) -> None:
        self.b = Box()


def mkh() -> Own[Holder]:
    return Holder()


def with_field_chain() -> None:
    # a `with` target read after the statement, borrowed through a field
    # chain off a fresh object
    with mkh().b.guard_m() as q:  # tpyc: ok
        q.append("body")
    junk = ["x" * 60 for _ in range(4)]
    q.append("after-" + str(len(junk)))
    print("fresh with", len(q), q[-2], q[-1])


def gen_with_field_chain() -> Iterator[str]:
    # ... and in a generator
    with mkh().b.guard_m() as q:  # tpyc: ok
        q.append("gen-body")
    junk = ["z" * 40 for _ in range(4)]
    yield q[-1] + "/" + str(len(junk))


class Inner:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]

    @property
    def items(self) -> list[int32]:
        return self.xs


class Mid:
    inner: Inner

    def __init__(self, n: int32) -> None:
        self.inner = Inner(n)

    @property
    def get_inner(self) -> Inner:
        return self.inner


class Outer:
    mid: Mid

    def __init__(self, n: int32) -> None:
        self.mid = Mid(n)


def prop_chain() -> None:
    # a `@property` read on a field of a fresh object, in a loop head
    for x in Outer(1).mid.get_inner.items:  # tpyc: ok
        print("fresh prop", x)


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Src:
    n: int32
    cell: Cell
    pair: tuple[int32, Cell]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.cell = Cell(n)
        # the tuple field holds its own Cell, which the warning names
        self.pair = (1, self.cell)  # tpyc: warning(/copies Cell into field/)


def pick(s: Src) -> Src:
    return s


def wrap_tup(s: Src) -> tuple[int32, Src]:
    return (1, s)


def use_gen(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


def walk_tup(t: tuple[int32, Cell]) -> Iterator[int32]:
    yield t[0] + t[1].n
    yield t[0] + t[1].n + 1


def gen_tuple_field() -> Iterator[int32]:
    # a borrow tuple field of a fresh object at a generator's parameter
    for v in walk_tup(pick(Src(41)).pair):  # tpyc: ok
        junk = [Src(999) for _ in range(10)]
        yield v + len(junk) - 10


def gen_tuple_element() -> Iterator[int32]:
    # ... and a borrow tuple's element
    for v in use_gen(wrap_tup(Src(45))[1]):  # tpyc: ok
        junk = [Src(999) for _ in range(10)]
        yield v + len(junk) - 10


kept: list[Cell] = []


async def co_own(c: Own[Cell]) -> int32:
    await asyncio.sleep(0)
    kept.append(c)
    return kept[-1].n


async def own_field() -> int32:
    # a fresh object's field taken over by an `Own` parameter, awaited and
    # kept by a task
    a = await co_own(Src(5).cell)  # tpyc: ok
    t = asyncio.create_task(co_own(Src(6).cell))  # tpyc: ok
    return a + await t


def main() -> None:
    with_field_chain()
    print("fresh gen with", list(gen_with_field_chain()))
    prop_chain()
    print("fresh tuple field", list(gen_tuple_field()))
    print("fresh tuple element", list(gen_tuple_element()))
    print("fresh own field", asyncio.run(own_field()), [c.n for c in kept])
