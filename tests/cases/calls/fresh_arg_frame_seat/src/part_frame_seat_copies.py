# What a pass copies out of a value kept past an await, or consumes inside
# the statement, holds no borrow of its temporary: a copied clause target,
# a lambda a call consumes or that copies its capture, an augmented value;
# a lambda body's own temporaries and names are its own.
# The copies into `list[Src]` are the intended, warned copy semantics.
import asyncio
from asyncio import Task
from typing import Callable, Iterator
from tpy import Own, StrView, int32


class Src:
    text: str
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.text = str(n) * 3
        self.xs = [n, n + 1]


class Ticker:
    step: int32

    def __init__(self, step: int32) -> None:
        self.step = step

    def ticks(self, s: Src) -> Iterator[int32]:
        yield s.xs[0] + self.step


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


async def owned(s: Src) -> str:
    await asyncio.sleep(0)
    return s.text


def gen_ref(s: Src) -> Iterator[Src]:
    yield s


def call(f: Callable[[], int32]) -> int32:
    return f()


async def apply(f: Callable[[int32], int32]) -> int32:
    await asyncio.sleep(0)
    return f(10) + f(20)


def gen(s: Src) -> Iterator[int32]:
    yield s.xs[0]
    yield s.xs[1]


async def co(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


def tasks(s: Src) -> Iterator[Own[Task[int32]]]:
    yield asyncio.create_task(co(s.xs[0]))


async def copied() -> None:
    out: list[Src] = []
    for i in range(3):
        # a reference target copied into owned storage, by loop and by
        # comprehension
        for x in gen_ref(Src(i * 10)):
            out.append(x)  # tpyc: warning(/copies Src/)
        out += [x for x in gen_ref(Src(i * 10 + 1))]  # tpyc: warning(/copies/)
        await asyncio.sleep(0)
    print("copies copied", [s.xs[0] for s in out])


async def in_pass() -> None:
    out: list[int32] = []
    for i in range(3):
        # a reference clause target read only inside the pass: a filter, an
        # inner clause, a called lambda (whose loop-rebind warning is
        # BUGS.md#loop-rebind-warning-for-consumed-lambda)
        out += [1 for x in gen_ref(Src(i)) if x.xs[0] >= 0]  # tpyc: ok
        out += [y for x in gen_ref(Src(i)) for y in x.xs]  # tpyc: ok
        out += [call(lambda: x.xs[1])  # tpyc: warning(/copies the object/)
                for x in gen_ref(Src(i))]
        await asyncio.sleep(0)
    print("copies in pass", out)


async def lambdas() -> None:
    fns: list[Callable[[], Callable[[], int32]]] = []
    for i in range(3):
        w = await view(Src(i))
        # a key lambda the call consumes reads the bound view
        keyed = sorted(["bb", "a", "ccc"], key=lambda x: len(x) * len(w))  # tpyc: ok
        print("copies key", keyed)
        # a nested lambda copies the clause target at both levels; the
        # outer lambda's warning speaks for both, and its loop-rebind
        # warning holds for a comprehension of several elements
        fns += [lambda: (lambda: r.xs[0])  # tpyc: warning(/copies the object/)
                for r in gen_ref(Src(i * 10))]
    print("copies nested", [fn()() for fn in fns])


async def lambda_scope() -> int32:
    # a comprehension in the lambda binds its own `v`
    f: Callable[[], int32] = lambda: len([v for v in range(2)])
    for i in range(3):
        v = await view(Src(i))  # tpyc: ok
        print("copies scope", v[:2])
    # an awaited operand's lambda seats its temporaries in its own body
    return f() + await apply(lambda i: len([x for x in gen(Src(i))]))  # tpyc: ok


async def augmented() -> None:
    acc = ""
    for i in range(3):
        # the augmented value is an operand of `__iadd__`, which copies it
        acc += await view(Src(i))  # tpyc: ok
        # a dict entry whose value is the await itself
        d = {"k": await owned(Src(i))}  # tpyc: ok
        acc += d["k"]
    print("copies aug", acc)


async def own_slot() -> int32:
    ts: list[Task[int32]] = []
    t = Ticker(100)
    for i in range(3):
        # an Own element and a method generator's int element carry no borrow
        ts += [k for k in tasks(Src(i))]  # tpyc: ok
        for n in t.ticks(Src(i)):  # tpyc: ok
            ts.append(asyncio.create_task(co(n)))
        await asyncio.sleep(0)
    total = 0
    for tk in ts:
        total += await tk
    return total


# `Own` lets the fresh list literal move in; the body only reads it, which
# the "never consumed" warning rightly says.
def call_first(fs: Own[list[Callable[[], int32]]]) -> int32:  # tpyc: warning(/never consumed/)
    return fs[0]()


def shadowed() -> int32:
    y = 42
    # the inner lambda's `y` is the comprehension's own binding, not the
    # outer lambda's capture: its copy warning stays
    outer: Callable[[], int32] = (
        lambda: call_first([lambda: y.xs[0] for y in [Src(y)]])  # tpyc: warning(/copies the object 'y'/)
    )
    return outer()


def main() -> None:
    asyncio.run(copied())
    asyncio.run(in_pass())
    asyncio.run(lambdas())
    asyncio.run(augmented())
    n = asyncio.run(lambda_scope())
    print("copies lambda scope", n)
    print("copies shadowed", shadowed())
    print("copies own slot", asyncio.run(own_slot()))
