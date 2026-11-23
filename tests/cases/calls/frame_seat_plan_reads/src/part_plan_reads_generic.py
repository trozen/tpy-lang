# A generic call result at an open `T` in a generic caller's loop head, a
# protocol parameter that is no iterable at a coroutine kept by a task, and
# a fresh list at an `Iterable` parameter whose result a frame keeps.
import asyncio
from typing import Callable, Iterable, Iterator, Protocol
from tpy import Own, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        self.n = -999

    def get_n(self) -> int32:
        return self.n


def gen_t[T](x: T) -> Iterator[T]:
    yield x
    yield x


def id_t[T](x: T) -> T:
    return x


def open_plain[T](x: T, touch: Callable[[T], int32]) -> int32:
    # an open `T` result is bound as the instantiation binds it, no copy:
    # `touch` writes through `v`, and the caller sees it
    s = 0
    for v in gen_t(id_t(x)):  # tpyc: ok
        s += touch(v)
    return s


def open_gen[T](x: T, touch: Callable[[T], int32]) -> Iterator[int32]:
    # ... in a generator caller
    s = 0
    for v in gen_t(id_t(x)):  # tpyc: ok
        s += touch(v)
        yield s


async def open_co[T](x: T, touch: Callable[[T], int32]) -> int32:
    # ... and a coroutine caller
    s = 0
    for v in gen_t(id_t(x)):  # tpyc: ok
        await asyncio.sleep(0)
        s += touch(v)
    return s


def poke(v: list[int32]) -> int32:
    v.append(9)
    return len(v)


def gsrc(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n


async def open_mono_co() -> int32:
    # an open `T` result in a monomorphic coroutine's head: the fresh
    # argument under it is what the frame keeps
    s = 0
    for v in gsrc(id_t(Src(5))):  # tpyc: ok
        await asyncio.sleep(0)
        s += v
    return s


def mk_t[T](v: T) -> Own[T]:
    # a borrowed `T` returned as owned copies it at a reference instantiation
    return v  # tpyc: warning(/may copy T/)


def own_gen[T](v: T) -> Iterator[int32]:
    # an owned `T` result is a fresh value the generator's head holds
    for y in gen_t(mk_t(v)):  # tpyc: ok
        yield 1


class HasN(Protocol):
    def get_n(self) -> int32: ...


def pick_second(a: HasN, b: Src) -> Src:
    return b


async def co_n(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.n


async def task_pick() -> int32:
    # a fresh argument at a protocol that is no iterable: the result does
    # not borrow it
    s = Src(42)
    t = asyncio.create_task(co_n(pick_second(Src(1), s)))  # tpyc: ok
    s.n = 43
    return await t


def first_item(xs: Iterable[Src]) -> Src:
    for x in xs:
        return x
    raise ValueError("empty")


def get_list() -> Own[list[Src]]:
    return [Src(10)]


def gen_src(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n


def first_head() -> Own[list[int32]]:
    # a fresh list a head keeps, at an `Iterable` the result borrows from
    out: list[int32] = []
    for v in gen_src(first_item(get_list())):  # tpyc: ok
        out.append(v)
    return out


async def first_head_co() -> int32:
    # ... across a suspension
    s = 0
    for v in gen_src(first_item(get_list())):  # tpyc: ok
        await asyncio.sleep(0)
        s += v
    return s


async def first_awaited() -> int32:
    # ... and as an awaited operand
    return await co_n(first_item(get_list()))  # tpyc: ok


class Picker:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def first(self, xs: Iterable[Src]) -> Src:
        for x in xs:
            return x
        raise ValueError("empty")


def first_method_head() -> Own[list[int32]]:
    # ... and a method's result
    p = Picker()
    out: list[int32] = []
    for v in gen_src(p.first(get_list())):  # tpyc: ok
        out.append(v)
    return out


def first_stmt() -> int32:
    # the same call consumed by its statement holds nothing
    return first_item(get_list()).n  # tpyc: ok


def first_lent() -> int32:
    # a lent list local is no temporary
    xs = [Src(4)]
    s = 0
    for v in gen_src(first_item(xs)):  # tpyc: ok
        xs[0].n = 5
        s += v
    return s * 10 + xs[0].n


def main() -> None:
    xs = [1, 2]
    print("generic open plain", open_plain(Src(10), lambda s: s.n),
          open_plain(5, lambda n: n), open_plain(xs, lambda v: poke(v)))
    print("generic open plain after", xs)
    print("generic open gen", list(open_gen(Src(1), lambda s: s.n)),
          list(open_gen(7, lambda n: n)),
          list(open_gen(xs, lambda v: poke(v))))
    print("generic open gen after", xs)
    print("generic open co", asyncio.run(open_co(Src(2), lambda s: s.n)),
          asyncio.run(open_co(8, lambda n: n)),
          asyncio.run(open_co(xs, lambda v: poke(v))))
    print("generic open co after", xs)
    xs.append(3)
    print("generic open mono co", asyncio.run(open_mono_co()))
    print("generic own gen", list(own_gen(xs)), list(own_gen(3)))
    print("generic task pick", asyncio.run(task_pick()))
    print("generic first head", first_head())
    print("generic first head co", asyncio.run(first_head_co()))
    print("generic first method head", first_method_head())
    print("generic first awaited", asyncio.run(first_awaited()))
    print("generic first stmt", first_stmt())
    print("generic first lent", first_lent())
