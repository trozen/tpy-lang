# An async def with a default arg applies the omitted default at every call
# position. `create_task` goes through the factory; an inline `await` and the
# synthetic `async with` / `async for` suspensions construct the frame ctor
# directly, so the ctor is a second C++ callee that must carry the same
# defaults. Every position is also called with an explicit argument, which
# must beat the default.
import asyncio
import defmod
from typing import Iterable
from tpy import Int32, Own


async def add(a: Int32, b: Int32 = 10) -> Int32:
    await asyncio.sleep(0)
    return a + b


# Generic (templated) coroutine with a default.
async def counted[T](it: Iterable[T], skip: Int32 = 7) -> Int32:
    await asyncio.sleep(0)
    c: Int32 = 0
    for _x in it:
        c += 1
    return c + skip


class Adder:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    async def add(self, a: Int32, b: Int32 = 10) -> Int32:
        await asyncio.sleep(0)
        return self.base + a + b


class CM:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    # A defaulted __aenter__ param: the synthetic async-with suspension
    # emplaces only the receiver, so only a ctor default can supply `bump`.
    async def __aenter__(self, bump: Int32 = 5) -> Int32:
        await asyncio.sleep(0)
        self.n += bump
        return self.n

    async def __aexit__(self, exc_type: None, exc: None,
                        tb: None) -> bool:
        await asyncio.sleep(0)
        return False


class CM2:
    hits: Int32
    seen: Int32

    def __init__(self) -> None:
        self.hits = 0
        self.seen = 0

    async def __aenter__(self) -> Int32:
        await asyncio.sleep(0)
        return 1

    # A defaulted 4th __aexit__ param: the synthetic exit suspension passes
    # the three monostate exception slots and nothing else, so `extra` can
    # only come from the frame ctor's default.
    async def __aexit__(self, exc_type: None, exc: None, tb: None,
                        extra: Int32 = 9) -> bool:
        await asyncio.sleep(0)
        self.seen = extra
        return False


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    # A defaulted __anext__ param: the synthetic async-for suspension
    # emplaces only the receiver, so `step` comes from the ctor default.
    async def __anext__(self, step: Int32 = 1) -> Int32:
        await asyncio.sleep(0)
        if self.n <= 0:
            raise StopAsyncIteration
        self.n -= step
        return self.n


class Counts:
    start: Int32

    def __init__(self, start: Int32) -> None:
        self.start = start

    def __aiter__(self) -> Own[Counter]:
        return Counter(self.start)


async def with_default() -> Int32:
    t = asyncio.create_task(add(5))       # default b=10 -> 15
    return await t


async def with_override() -> Int32:
    t = asyncio.create_task(add(5, 2))    # override -> 7
    return await t


# inline await, free async def -- the reproducer
async def inline_default() -> Int32:
    return await add(5)                   # tpyc: ok


# inline await, explicit argument beats the default
async def inline_override() -> Int32:
    return await add(5, 2)                # tpyc: ok


# generic async def, inline await
async def generic_default() -> Int32:
    nums: list[Int32] = [1, 2, 3]
    return await counted(nums)            # tpyc: ok


# generic async def, inline await, explicit argument beats the default
async def generic_override() -> Int32:
    nums: list[Int32] = [1, 2, 3]
    return await counted(nums, 3)         # tpyc: ok


# async METHOD, inline await
async def method_inline_default() -> Int32:
    ad = Adder(100)
    return await ad.add(5)                # tpyc: ok


# async METHOD, explicit argument beats the default
async def method_inline_override() -> Int32:
    ad = Adder(100)
    return await ad.add(5, 2)             # tpyc: ok


# async METHOD, create_task (the factory path)
async def method_task_default() -> Int32:
    ad = Adder(100)
    t = asyncio.create_task(ad.add(5))    # tpyc: ok
    return await t


# async METHOD, create_task, explicit argument beats the default
async def method_task_override() -> Int32:
    ad = Adder(100)
    t = asyncio.create_task(ad.add(5, 2))  # tpyc: ok
    return await t


# defaulted __aenter__ under async with
async def aenter_default() -> Int32:
    cm = CM()
    out: Int32 = 0
    async with cm as v:                   # tpyc: ok
        out = v
    return out


# defaulted __aexit__ under async with
async def aexit_default() -> Int32:
    cm = CM2()
    async with cm:                        # tpyc: ok
        cm.hits += 1
    return cm.seen


# defaulted __anext__ under async for
async def anext_default() -> Int32:
    c = Counts(4)
    total: Int32 = 0
    async for x in c:                     # tpyc: ok
        total += x
    return total


# cross-module: the default names the CALLEE module's Final constant
async def cross_module_default() -> Int32:
    return await defmod.scaled(5)         # tpyc: ok


# cross-module, explicit argument beats the default
async def cross_module_override() -> Int32:
    return await defmod.scaled(5, 7)      # tpyc: ok


def main() -> None:
    print("task-default", asyncio.run(with_default()))
    print("task-override", asyncio.run(with_override()))
    print("inline-default", asyncio.run(inline_default()))
    print("inline-override", asyncio.run(inline_override()))
    print("generic-inline", asyncio.run(generic_default()))
    print("generic-inline-override", asyncio.run(generic_override()))
    print("method-inline", asyncio.run(method_inline_default()))
    print("method-inline-override", asyncio.run(method_inline_override()))
    print("method-task", asyncio.run(method_task_default()))
    print("method-task-override", asyncio.run(method_task_override()))
    print("aenter", asyncio.run(aenter_default()))
    print("aexit-extra", asyncio.run(aexit_default()))
    print("anext", asyncio.run(anext_default()))
    print("xmod", asyncio.run(cross_module_default()))
    print("xmod-override", asyncio.run(cross_module_override()))


main()
