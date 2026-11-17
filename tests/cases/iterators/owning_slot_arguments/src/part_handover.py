# Frame callees by argument owner: tasks kept past the loop pass that built
# them each own the container their pass handed over; a borrowed parameter is
# lent (warned) and stays the caller's; a narrowed Optional local at its last
# use hands its container over; an owned `bytes` step is the value itself. A
# local a later binding points at existing storage is lent at the call, and
# so is a borrowed parameter passed to a free callee (bound `const&`). A loop
# target over an `Own` step and an owned local at an instantiation hand their
# container over at their last use.
import asyncio
from asyncio import Task
from typing import Iterable, Iterator
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    rs: list[R]

    def __init__(self) -> None:
        self.rs = [R(5)]


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def take(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def gen(xs: Iterable[Own[R]]) -> Iterator[int32]:
    for x in xs:
        yield x.v


async def atake(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


async def atake_slow(n: int32, xs: Iterable[Own[R]]) -> int32:
    # Suspends between elements, so the tasks of one loop interleave.
    t = n
    for x in xs:
        await asyncio.sleep(0)
        t += x.v
    return t


def gen_param(xs: list[R]) -> Own[list[int32]]:
    # A borrowed parameter is lent, not handed over.
    return list(gen(xs))  # tpyc: warning(/copies R elements/)


async def aparam(xs: list[R]) -> int32:
    # A borrowed parameter is lent, not handed over.
    return await atake(xs)  # tpyc: warning(/copies R elements/)


def take_param(xs: list[R]) -> int32:
    # Only lent to the owning slot: the parameter is never written.
    return take(xs)  # tpyc: warning(/copies R elements/)


def rebound_later(h: H) -> int32:
    xs = mk()
    # The later binding below makes `xs` refer to existing storage: lent here.
    t = take(xs)  # tpyc: warning(/copies R elements/)
    xs = h.rs
    xs[0].v = 6
    return t + len(xs)


def rebound_later_gen(h: H) -> Own[list[int32]]:
    xs = mk()
    # The later binding below makes `xs` refer to existing storage: lent here.
    it = gen(xs)  # tpyc: warning(/copies R elements/)
    xs = h.rs
    xs[0].v = 7
    return list(it)


def gen_narrow(c: bool) -> Own[list[int32]]:
    o: list[R] | None = None
    if c:
        o = mk()
    if o is not None:
        # A narrowed local at its last use hands its container over.
        return list(gen(o))  # tpyc: ok
    return []


async def anarrow(c: bool) -> int32:
    o: list[R] | None = None
    if c:
        o = mk()
    if o is not None:
        return await atake(o)  # tpyc: ok
    return -1


def step_free(src: Iterable[Own[list[R]]]) -> int32:
    t = 0
    for xs in src:
        # The loop target owns its container: handed over at its last use.
        t += take(xs)  # tpyc: ok
    return t


def step_comp(src: Iterable[Own[list[R]]]) -> Own[list[int32]]:
    return [take(xs) for xs in src]  # tpyc: ok


def step_gen(src: Iterable[Own[list[R]]]) -> Iterator[int32]:
    for xs in src:
        for v in gen(xs):  # tpyc: ok
            yield v


def inst_dead() -> Own[list[R]]:
    xs = mk()
    # A dead owned local hands its container over to the new list.
    return list[R](xs)  # tpyc: ok


def gen_bytes(xs: Iterable[Own[bytes]]) -> Iterator[bytes]:
    for b in xs:  # tpyc: ok
        yield b


async def akept() -> None:
    # Each task outlives its loop pass; every frame owns its own container.
    tasks: list[Task[int32]] = []
    for i in range(1, 4):
        tasks.append(asyncio.create_task(atake_slow(i, [R(i), R(i * 10)])))  # tpyc: ok
    for i in range(1, 3):
        tasks.append(asyncio.create_task(atake_slow(i * 100, mk())))  # tpyc: ok
    for t in tasks:
        print("kept task", await t)
    ys = [R(1), R(2)]
    print("async param", await aparam(ys), ys[0].v, len(ys))
    ys[1].v = 8
    print("async param after", ys[1].v)
    print("async narrow", await anarrow(True), await anarrow(False))


def main() -> None:
    bs = [b"p", b"q"]
    print("gen bytes", list(gen_bytes(bs)), list(gen_bytes([b"r"])), bs)  # tpyc: ok
    ps = [R(1), R(2)]
    print("gen param", gen_param(ps), len(ps))
    # The caller keeps its elements and mutates them afterwards.
    ps[0].v = 7
    print("gen param after", ps[0].v, ps[1].v)
    print("gen narrow", gen_narrow(True), gen_narrow(False))
    qs = [R(3), R(4)]
    print("free param", take_param(qs), len(qs))
    # The caller keeps its elements and mutates them afterwards.
    qs[0].v = 9
    print("free param after", qs[0].v, qs[1].v)
    h = H()
    print("rebound later", rebound_later(h), h.rs[0].v)
    print("rebound later gen", rebound_later_gen(h), h.rs[0].v)
    a: list[list[R]] = [[R(1), R(2)], [R(3)]]
    print("step free", step_free(a))
    b: list[list[R]] = [[R(1)], [R(4)]]
    print("step comp", step_comp(b))
    c: list[list[R]] = [[R(5)], [R(6), R(7)]]
    print("step gen", list(step_gen(c)))
    ys = inst_dead()
    ys[0].v = 9
    print("inst dead", ys[0].v, ys[1].v, len(ys))
    asyncio.run(akept())
