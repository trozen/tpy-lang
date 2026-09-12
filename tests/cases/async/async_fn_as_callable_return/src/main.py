# An `async def` can be handed back in RETURN position typed as a coroutine
# factory `Callable[[...], Own[Cancellable[T]]]` -- the same wrapper synthesis
# as the argument/field positions, in a return coercion context.
import asyncio
from typing import Callable
from tpy import int32, Own
from tpy.coro import Cancellable


async def triple(n: int32) -> int32:
    await asyncio.sleep(0.0)
    return n + n + n


def pick() -> Callable[[int32], Own[Cancellable[int32]]]:
    return triple


async def main_coro() -> int32:
    factory = pick()
    return await asyncio.create_task(factory(7))


def main() -> None:
    print(asyncio.run(main_coro()))


main()
