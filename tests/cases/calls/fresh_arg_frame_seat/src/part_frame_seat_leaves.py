# A value handed on as a copy holds no borrow of the temporary it was read
# from only where every leaf that could point is copied: an explicit
# `copy()`, a container literal's copied element, a `str` a frame's loop
# variable owns, a tuple member that carries none. A borrow-form tuple a
# lambda captures, or a tuple a coroutine takes with a passed leaf, keeps
# its temporary on the frame; a generator a loop consumes keeps it for the
# statement, and so does a generator argument hoisted ahead of its call,
# down to the temporaries nested in it. The copies into `list[Src]` are
# the intended, warned copies.
import asyncio
from asyncio import Task
from typing import Callable, Iterator
from tpy import Own, copy, int32


class Src:
    n: int32
    text: str

    def __init__(self, n: int32) -> None:
        self.n = n
        self.text = "item-" + str(n) * 3


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    box: Box

    def __init__(self, n: int32) -> None:
        self.box = Box(n)


def pick(h: Holder) -> Holder:
    return h


def use_gen(b: Box) -> Iterator[int32]:
    yield b.n


def walk(h: Holder) -> Iterator[int32]:
    yield h.box.n
    yield h.box.n + 1


def drain(g: Iterator[int32]) -> int32:
    total = 0
    for v in g:
        total += v
    return total


async def tick() -> int32:
    await asyncio.sleep(0)
    return 10


class Plain:
    n: int32

    def __init__(self, s: Src) -> None:
        self.n = s.n


def gen_ref(s: Src) -> Iterator[Src]:
    yield s


def gen_str(s: Src) -> Iterator[str]:
    yield s.text


def gen_pairs(s: Src) -> Iterator[tuple[int32, Src]]:
    yield (s.n, s)


def gen_twins(s: Src) -> Iterator[tuple[Src, Src]]:
    yield (s, s)


def mk_tup(s: Src) -> tuple[int32, Own[Box]]:
    return (1, Box(s.n))


def mk_own(s: Src) -> Own[Box]:
    return Box(s.n)


async def co_n(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


async def co_s(s: str) -> str:
    await asyncio.sleep(0)
    return s[:6]


# The `Own` element is the copied leaf beside the passed one; reading it in
# place leaves it unconsumed, which the warning says.
async def co_mixed(p: tuple[Own[Src], Src]) -> int32:  # tpyc: warning(/never consumed/)
    await asyncio.sleep(0)
    return p[0].n + p[1].n


async def copies() -> None:
    out: list[Src] = []
    nested: list[list[Src]] = []
    for i in range(3):
        # an explicit copy, by loop and by comprehension
        for x in gen_ref(Src(i * 10)):
            out.append(copy(x))  # tpyc: ok
        out += [copy(x) for x in gen_ref(Src(i * 10 + 1))]  # tpyc: ok
        # a container literal's element copied into owned storage
        for x in gen_ref(Src(i * 10 + 2)):
            nested.append([x])  # tpyc: warning(/copies Src/)
        await asyncio.sleep(0)
    print("leaves copies", [s.n for s in out], [s[0].n for s in nested])


async def strs() -> None:
    ts: list[Task[str]] = []
    keep: list[str] = []
    for i in range(3):
        # a frame's loop variable owns its `str`: a task and a list copy it
        for v in gen_str(Src(i)):  # tpyc: ok
            ts.append(asyncio.create_task(co_s(v)))
            keep.append(v)
        keep += [w for w in gen_str(Src(i + 5))]  # tpyc: ok
        await asyncio.sleep(0)
    got: list[str] = []
    for t in ts:
        got.append(await t)
    print("leaves strs", got, keep)


async def members() -> int32:
    ts: list[Task[int32]] = []
    for i in range(3):
        # only the int member of an unpack clause escapes
        ts += [asyncio.create_task(co_n(k))
               for k, s in gen_pairs(Src(i))]  # tpyc: ok
        await asyncio.sleep(0)
    total = 0
    for tk in ts:
        total += await tk
    return total


async def frame_kept() -> int32:
    fns: list[Callable[[], int32]] = []
    ts: list[Task[int32]] = []
    # a lambda capturing a borrow-form tuple, and a tuple with a passed
    # leaf a task takes, keep their temporary on the frame; the lambda's
    # copy and rebind warnings and the task's copy of the `Own` leaf are
    # the warned semantics of those captures and arguments
    for t in gen_pairs(Src(42)):  # tpyc: ok
        fns.append(lambda: t[1].n)  # tpyc: warning(/own copy of 't'/) warning(/bound again/)
    for p in gen_twins(Src(10)):  # tpyc: ok
        ts.append(asyncio.create_task(co_mixed(p)))  # tpyc: warning(/copies tuple/)
    await asyncio.sleep(0)
    first = ts[0]
    return fns[0]() + await first


async def results() -> int32:
    total = 0
    for i in range(3):
        # an `Own` tuple element, an `Own` result and a pointer-free
        # constructor hold no borrow of their argument
        tup = mk_tup(Src(i))  # tpyc: ok
        b = mk_own(Src(i * 10))  # tpyc: ok
        p = Plain(Src(i * 100))  # tpyc: ok
        await asyncio.sleep(0)
        total += tup[1].n + b.n + p.n
    return total


async def consumed() -> int32:
    total = 0
    for i in range(3):
        # the loop consumes the generator inside its statement, so the
        # temporary its argument borrows lives as long
        for x in use_gen(pick(Holder(i)).box):  # tpyc: ok
            total += x
        await asyncio.sleep(0)
    return total


def seated() -> int32:
    # a fresh argument nested in a generator argument the call hoists lives
    # for the statement: a call argument, a field receiver
    a = drain(walk(Holder(5)))  # tpyc: ok
    b = drain(walk(pick(Holder(5))))  # tpyc: ok
    return a + b + drain(use_gen(pick(Holder(6)).box))  # tpyc: ok


def seated_gen() -> Iterator[int32]:
    yield drain(walk(pick(Holder(7))))  # tpyc: ok


async def seated_co() -> int32:
    # ... with an await in the same statement
    return drain(use_gen(pick(Holder(8)).box)) + await tick()  # tpyc: ok


def main() -> None:
    asyncio.run(copies())
    asyncio.run(strs())
    n = asyncio.run(members())
    print("leaves members", n)
    n = asyncio.run(frame_kept())
    print("leaves frame kept", n)
    n = asyncio.run(results())
    print("leaves results", n)
    n = asyncio.run(consumed())
    print("leaves consumed", n)
    print("leaves seated", seated(), list(seated_gen()), asyncio.run(seated_co()))
