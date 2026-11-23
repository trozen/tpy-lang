# An awaited method whose receiver is reached through a fresh object -- a
# Deref wrapper, an element of a fresh container: the frame holds that object
# while the coroutine runs. An unproven Optional wrapper is checked before
# its `__deref__()`.
import asyncio

from tpy import Own, int32, nocopy, readonly
from tplib.box import Box
from tplib.rc import Rc


@nocopy
class Acc:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        # Fresh allocations after the suspension reuse any storage the
        # receiver had freed; the write and the read go through the
        # receiver itself.
        junk = [Acc(999) for _ in range(4)]
        self.base += x + len(junk) - 4
        return self.base

    @readonly
    async def read(self) -> int32:
        await asyncio.sleep(0.0)
        junk = [Acc(999) for _ in range(4)]
        return self.base + len(junk) - 4


def mk_box(n: int32) -> Own[Box[Acc]]:
    return Box(Acc(n))


def mk_rc(n: int32) -> Own[Rc[Acc]]:
    return Rc.new(Acc(n))


def mk_list(n: int32) -> Own[list[Acc]]:
    return [Acc(n), Acc(n + 1)]


def mk_dict(n: int32) -> Own[dict[str, Acc]]:
    d: dict[str, Acc] = {}
    d["k"] = Acc(n)
    return d


async def wrappers(n: int32) -> None:
    # A constructed and a returned Box, and a returned Rc: the frame holds
    # the wrapper the `__deref__()` reads into.
    a = await Box(Acc(n)).read()  # tpyc: ok
    b = await mk_box(n + 10).add(6)  # tpyc: ok
    c = await mk_rc(n + 20).read()  # tpyc: ok
    print("fresh_wrapper", a, b, c)


async def elements(n: int32) -> None:
    # An element of a fresh list or dict: the frame holds the container.
    a = await mk_list(n)[0].read()  # tpyc: ok
    b = await mk_list(n)[1].add(5)  # tpyc: ok
    c = await mk_dict(n + 30)["k"].read()  # tpyc: ok
    print("fresh_element", a, b, c)


async def opt_box(b: Box[Acc] | None) -> int32:
    # An unproven Optional wrapper: checked before its `__deref__()`.
    return await b.add(3)  # tpyc: warning(/Potential None access/)


def main() -> None:
    asyncio.run(wrappers(1))
    asyncio.run(elements(2))
    b = Box(Acc(40))
    print("fresh_opt_box", asyncio.run(opt_box(b)), b.base)
    try:
        asyncio.run(opt_box(None))
    except AttributeError:
        print("fresh_opt_box_none AttributeError")
