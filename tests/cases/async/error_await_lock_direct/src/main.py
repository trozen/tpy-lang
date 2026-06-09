# A Lock/Semaphore is not awaitable directly (matching CPython, which raises
# TypeError) -- acquisition goes through acquire() / async with. The private
# _LockAcquire awaitable keeps __poll__ off the public Lock type.
import asyncio
from asyncio import Lock


async def main_coro() -> None:
    lock = Lock()
    await lock  # tpyc: error(/await operand must be/)


def main() -> None:
    asyncio.run(main_coro())


main()
