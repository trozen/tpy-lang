# A fresh field-read receiver in a compound head -- a generator's argument
# a loop drains across yields -- is kept for the statement, as a method's
# fresh receiver is; so is one in a `match` arm and an `elif` condition.
# A list or a comprehension at a frame's `Iterable` parameter is held as
# any fresh argument is.
import asyncio
from typing import Iterable, Iterator, Self
from tpy import int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def me(self) -> Self:
        return self


class Holder:
    s: Src

    def __init__(self, n: int32) -> None:
        self.s = Src(n)


class Box:
    s: Src

    def __init__(self, s: Src) -> None:
        # Box holds its own Src: the warned copy is the semantics here.
        self.s = s  # tpyc: warning(/copies Src into field/)


def gen(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


def consume(g: Iterator[int32]) -> int32:
    total = 0
    junk = [Src(999) for _ in range(10)]
    for v in g:
        total += v
    return total + len(junk) - 10


def walk_method() -> Iterator[int32]:
    for x in gen(Src(25).me()):  # tpyc: ok
        yield x


def walk_field() -> Iterator[int32]:
    for x in gen(Holder(10).s):  # tpyc: ok
        yield x


def walk_box() -> Iterator[int32]:
    for x in gen(Box(Src(30)).s):  # tpyc: ok
        yield x


def matcher(k: int32) -> int32:
    match k:
        case 1:
            return consume(gen(Holder(7).s))  # tpyc: ok
        case _:
            return 0


def elifp(k: int32) -> int32:
    if k == 0:
        return 0
    elif consume(gen(Holder(9).s)) > 0:  # tpyc: ok
        return consume(gen(Holder(10).s))
    return 1


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


async def drain_it(it: Iterable[int32]) -> int32:
    t = 0
    for x in it:
        await asyncio.sleep(0)
        t += x
    return t


def comp_plain() -> int32:
    # a comprehension at a generator's `Iterable` parameter, plain body
    return sum(g_it([x for x in range(3)]))  # tpyc: ok


def comp_gen_head() -> Iterator[int32]:
    # ... in a generator's loop head, and the list literal twin
    for y in g_it([x * 2 for x in range(3)]):  # tpyc: ok
        yield y
    for y in g_it([7, 8]):  # tpyc: ok
        yield y


async def comp_awaited() -> int32:
    # ... awaited, and kept by a task past its statement
    f = await drain_it([x for x in range(4)])  # tpyc: ok
    t = asyncio.create_task(drain_it([x for x in range(5)]))  # tpyc: ok
    junk = [Src(999) for _ in range(10)]
    return f + await t + len(junk) - 10


def main() -> None:
    print("heads comp plain", comp_plain())
    print("heads comp gen", list(comp_gen_head()))
    print("heads comp co", asyncio.run(comp_awaited()))
    print("heads method", list(walk_method()))
    print("heads field", list(walk_field()))
    print("heads box", list(walk_box()))
    print("heads match", matcher(1))
    print("heads elif", elifp(1))
