# await t where t: Task[None] -- exercises Task<std::monostate>'s
# poll consumption path. With the position-aware-None fix the synth
# coroutine's __poll__ returns Poll<std::monostate> and the surrounding
# Task<std::monostate> consumes it; pre-fix this path used Task<void> /
# Poll<void> exclusively.
import asyncio
from tpy.coro import Task


async def background() -> None:
    print("background ran")


async def main_coro() -> None:
    t: Task[None] = asyncio.create_task(background())
    await t
    print("awaited")


def main() -> None:
    asyncio.run(main_coro())


main()
