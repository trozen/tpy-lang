# Generator and coroutine callees: a container handed over passes straight
# into the frame, which owns it; a lent one stays the caller's object, which
# the frame sees mutated.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def gen(xs: Iterable[Own[R]]) -> Iterator[int32]:
    for x in xs:
        yield x.v


def gen_alias(xs: Iterable[Own[R]]) -> Iterator[int32]:
    # A second name in a frame is the parameter it forwards.
    b = xs  # tpyc: ok
    for x in b:
        yield x.v


async def atake(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def gen_n(n: int32, xs: Iterable[Own[R]]) -> Iterator[int32]:
    for x in xs:
        yield n + x.v


async def atake_n(n: int32, xs: Iterable[Own[R]]) -> int32:
    t = n
    for x in xs:
        t += x.v
    return t


def gen_values(xs: Iterable[Own[int32]]) -> Iterator[int32]:
    # An owned value step is the value itself.
    for v in xs:  # tpyc: ok
        yield v


def gen_strs(xs: Iterable[Own[str]]) -> Iterator[str]:
    for s in xs:  # tpyc: ok
        yield s


async def asum_values(xs: Iterable[Own[int32]]) -> int32:
    t = 0
    for v in xs:  # tpyc: ok
        await asyncio.sleep(0)
        t += v
    return t


async def alen_strs(xs: Iterable[Own[str]]) -> int32:
    n = 0
    for s in xs:  # tpyc: ok
        n += len(s)
    await asyncio.sleep(0)
    return n


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def gen_caller() -> Iterator[int32]:
    # A generator caller's fresh argument is handed over to the frame it
    # iterates.
    for y in gen(mk()):  # tpyc: ok
        yield y
    # A fresh comprehension is seated on a frame local and moved in.
    for z in gen([R(i) for i in range(3)]):  # tpyc: ok
        yield z


def gen_loop_caller() -> Iterator[int32]:
    # Each pass of a loop hands its own fresh argument over: the seat is
    # moved from, so no handle borrows it.
    for i in range(2):
        for j, y in enumerate(gen_n(i, mk())):  # tpyc: ok
            yield y * 10 + j


def gen_select_caller(c: bool) -> Iterator[int32]:
    # A ternary or `or` over fresh containers is handed over as one.
    for y in gen(mk() if c else [R(9)]):  # tpyc: ok
        yield y
    for z in gen(mk() or [R(8)]):  # tpyc: ok
        yield z


async def amain() -> None:
    # A task per pass, each owning the container its pass handed over.
    for i in range(2):
        task = asyncio.create_task(atake_n(i, mk()))  # tpyc: ok
        print("async loop task", await task)
    print("async values", await asum_values([1, 2, 3]), await alen_strs(["ab", "c"]))  # tpyc: ok
    print("async fresh", await atake(mk()))  # tpyc: ok
    d = [R(3), R(4)]
    print("async dead", await atake(d))  # tpyc: ok
    print("async literal", await atake([R(5)]))  # tpyc: ok
    print("async comprehension", await atake([R(i) for i in range(4)]))  # tpyc: ok


def main() -> None:
    print("gen fresh", list(gen(mk())))  # tpyc: ok
    print("gen literal", list(gen([R(1), R(2)])))  # tpyc: ok
    d = [R(1), R(2)]
    print("gen dead", list(gen(d)))  # tpyc: ok
    xs = [R(1), R(2)]
    # Lent: the frame holds the caller's list, mutated before it runs.
    g = gen(xs)  # tpyc: warning(/copies R elements/)
    xs.append(R(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    xs[0].v = 9
    print("gen lent", list(g))
    print("gen caller", list(gen_caller()))
    print("gen alias", list(gen_alias(mk())))
    print("gen loop caller", list(gen_loop_caller()))
    print("gen select caller", list(gen_select_caller(True)), list(gen_select_caller(False)))
    # A generator over a fresh argument kept past its statement owns it.
    k = gen(mk())  # tpyc: ok
    src = [R(1), R(2)]
    kc = gen([R(r.v + 1) for r in src])  # tpyc: ok
    # Mutating the comprehension's source afterwards leaves the frame's
    # own container alone.
    src[0].v = 50
    src.append(R(60))
    print("gen kept", list(k), list(kc), src[0].v)
    vs = [4, 5]
    print("gen values", list(gen_values(vs)), list(gen_values([6])), vs)  # tpyc: ok
    ss = ["x", "y"]
    print("gen strs", list(gen_strs(ss)), list(gen_strs(["z"])), ss)  # tpyc: ok
    asyncio.run(amain())
