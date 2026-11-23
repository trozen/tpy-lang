# An awaited call's arguments take the argument dispatch any call takes:
# borrow-tuple results and elements seat what they borrow for the await,
# an element of a borrow tuple over a local borrows the local, a frame that
# copies its argument in (`str` at an open `T`) seats nothing, and an
# `async def` named at a `Callable` slot is wrapped as a factory.
import asyncio
from typing import Callable
from tpy import Own, int32
from tpy.coro import Cancellable


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def wrap_tup(s: Src) -> tuple[int32, Src]:
    return (1, s)


def wrap_opt(s: Src) -> tuple[int32, Src | None]:
    return (1, s)


def mk(i: int32) -> str:
    return "item-" + str(i)


async def use(s: Src) -> int32:
    await asyncio.sleep(0)
    s.n += 1
    return s.n


async def use_opt(s: Src | None) -> int32:
    await asyncio.sleep(0)
    if s is not None:
        return s.n
    return 0


def peek(s: Src) -> int32:
    s.n += 1
    return s.n


async def co_tup(t: tuple[int32, Src]) -> int32:
    await asyncio.sleep(0)
    return t[0] + t[1].n


async def co_g[T](x: T) -> T:
    await asyncio.sleep(0)
    return x


async def double(x: int32) -> int32:
    await asyncio.sleep(0)
    return x * 2


async def run_twice(factory: Callable[[int32], Own[Cancellable[int32]]],
                    a: int32, b: int32) -> int32:
    t1 = asyncio.create_task(factory(a))
    t2 = asyncio.create_task(factory(b))
    return await t1 + await t2


async def body() -> None:
    s = Src(30)
    # A borrow-tuple call result handed to an awaited coroutine.
    print("await btup local", await co_tup(wrap_tup(s)))  # tpyc: ok
    print("await btup fresh", await co_tup(wrap_tup(Src(40))))  # tpyc: ok
    # Its element over a fresh object: the object is seated for the await.
    print("await elem fresh", await use(wrap_tup(Src(10))[1]))  # tpyc: ok
    print("await elem opt", await use_opt(wrap_opt(Src(20))[1]))  # tpyc: ok
    # Its element over a local borrows the local, which the callee writes.
    print("await elem local", await use(wrap_tup(s)[1]), s.n)  # tpyc: ok
    t = asyncio.create_task(use(wrap_tup(s)[1]))  # tpyc: ok
    print("task elem local", await t, s.n)
    # The plain twin: the element is the local itself, not a copy.
    print("plain elem local", peek(wrap_tup(s)[1]), s.n)  # tpyc: ok
    # An open `T` at `str`: the frame copies the string in.
    print("await generic str", await co_g(mk(1)))  # tpyc: ok
    print("await factory", await run_twice(double, 3, 5))  # tpyc: ok


def main() -> None:
    asyncio.run(body())
    # The same factory argument at a call that is not awaited.
    print("run factory", asyncio.run(run_twice(double, 1, 2)))  # tpyc: ok
