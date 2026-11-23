# A borrow tuple a generator takes, passed whole, as an element or as a
# field read, consumed in its statement: the `Src` its pointer borrows
# lives for the statement, as a scalar argument's does -- in a plain
# function, a generator and a coroutine.
import asyncio
from typing import Iterator
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Src:
    n: int32
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.pair = (1, Box(n))


def pick(s: Src) -> Src:
    return s


def wrap_tup(s: Src) -> tuple[int32, Src]:
    return (s.n, s)


def wrap_opt(s: Src) -> tuple[int32, Src | None]:
    return (s.n, s)


def gen_tup(t: tuple[int32, Src]) -> Iterator[int32]:
    yield t[1].n
    yield t[1].n + 1


def use_gen(s: Src) -> Iterator[int32]:
    yield s.n


def use_opt(s: Src | None) -> Iterator[int32]:
    if s is not None:
        yield s.n


def walk_tup(t: tuple[int32, Box]) -> Iterator[int32]:
    yield t[0] + t[1].n


def consume(g: Iterator[int32]) -> int32:
    total = 0
    junk = [Src(999) for _ in range(10)]
    for v in g:
        total += v
    return total + len(junk) - 10


def bound() -> int32:
    # the bound twin in a plain function, as `h = gen(pick(Src(7)))`
    g = gen_tup(wrap_tup(Src(42)))  # tpyc: ok
    return consume(g)


def in_gen() -> Iterator[int32]:
    yield consume(gen_tup(wrap_tup(Src(43))))  # tpyc: ok
    yield consume(use_gen(wrap_tup(Src(45))[1]))  # tpyc: ok
    yield consume(use_opt(wrap_opt(Src(46))[1]))  # tpyc: ok


async def tick() -> int32:
    await asyncio.sleep(0)
    return 10


async def in_co() -> int32:
    a = consume(use_gen(wrap_tup(Src(47))[1])) + await tick()  # tpyc: ok
    b = consume(use_opt(wrap_opt(Src(48))[1])) + await tick()  # tpyc: ok
    return a + b + consume(gen_tup(wrap_tup(Src(44)))) + await tick()  # tpyc: ok


def main() -> None:
    print("tuples whole", consume(gen_tup(wrap_tup(Src(42)))))  # tpyc: ok
    print("tuples elem", consume(use_gen(wrap_tup(Src(42))[1])))  # tpyc: ok
    print("tuples opt elem", consume(use_opt(wrap_opt(Src(42))[1])))  # tpyc: ok
    print("tuples field", consume(walk_tup(pick(Src(41)).pair)))  # tpyc: ok
    print("tuples bound", bound())
    print("tuples gen", list(in_gen()))
    print("tuples co", asyncio.run(in_co()))
