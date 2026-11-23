# An awaited call's operand: what the await hands back decides how long it
# lives -- consumed in its statement, or carried where the result goes.
import asyncio
from typing import Iterable
from tpy import Own, StrView, int32


class Src:
    text: str

    def __init__(self, n: int32) -> None:
        self.text = str(n) * 60


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


def mks() -> str:
    return "a" * 60


def sp(s: str) -> StrView:
    return s


async def values(s: StrView) -> Own[list[int32]]:
    await asyncio.sleep(0)
    return [len(s), ord(s[0])]


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk(n: int32) -> Own[list[R]]:
    return [R(n), R(n + 1)]


async def atake(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


async def consumed() -> None:
    for i in range(3):
        # a view the await hands back, read inside the statement
        print("lifted view", (await view(Src(i)))[0])  # tpyc: ok


async def nested_view() -> int32:
    # the fresh string under a seated view lives on the frame too
    result = await values(sp(mks()))  # tpyc: ok
    return result[0] + result[1]


async def iterators() -> None:
    # an iterator over a fresh container, handed to an awaited coroutine
    a = await atake(iter(mk(2)))  # tpyc: ok
    other = mk(4)
    f = True
    b = await atake(iter(mk(2) if f else other))  # tpyc: ok
    c = await atake(iter([R(i) for i in range(2, 4)]))  # tpyc: ok
    task = asyncio.create_task(atake(iter(mk(2))))  # tpyc: ok
    junk = [[k] * 30 for k in range(20)]
    d = await task
    print("lifted iter", a, b, c, d, len(junk))


async def lifted_args() -> None:
    # a lifted await read as a call's argument
    print("lifted arg", await atake(iter(mk(2))))  # tpyc: ok
    other = mk(4)
    f = True
    print("lifted arg cond", await atake(iter(mk(2) if f else other)))  # tpyc: ok
    print("lifted arg comp", await atake(iter([R(i) for i in range(2, 4)])))  # tpyc: ok


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def solo[T](x: T) -> tuple[T, int32]:
    await asyncio.sleep(0)
    return (x, 1)


async def value_out() -> int32:
    # a value read off the frame-kept temporary leaves the coroutine; the
    # borrow itself does not (error_frame_seat_awaited_borrow_escapes)
    return (await solo(Box(99)))[0].v  # tpyc: ok


def main() -> None:
    print("lifted value out", asyncio.run(value_out()))
    asyncio.run(consumed())
    asyncio.run(lifted_args())
    n = asyncio.run(nested_view())
    print("lifted nested view", n)
    asyncio.run(iterators())
