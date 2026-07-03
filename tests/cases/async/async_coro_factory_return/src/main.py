# Sync coroutine factories: `return f(...)` erases into the declared
# Own[Cancellable[T]] return slot; a factory result binds in sync context;
# a Callable-typed factory's result binds inside an async body and spawns.
from typing import Callable
import asyncio
from tpy import Own
from tpy.coro import Cancellable


async def add_one(n: int) -> int:
    return n + 1


def make(n: int) -> Own[Cancellable[int]]:
    return add_one(n)


async def spawn_via(factory: Callable[[int], Own[Cancellable[int]]], n: int) -> int:
    coro = factory(n)
    t = asyncio.create_task(coro)
    return await t


def main() -> None:
    c = make(41)
    print(asyncio.run(c))
    print(asyncio.run(spawn_via(make, 20)))


main()
