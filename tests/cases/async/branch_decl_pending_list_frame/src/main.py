# A list literal first declared inside an if/else branch of an async
# body resolves in the hoisted frame field and survives the suspension.
import asyncio


async def pick(n: int) -> int:
    if n > 2:
        xs = [1, 2]
    else:
        xs = [3]
    await asyncio.sleep(0)
    return xs[0] + len(xs)


async def drive() -> None:
    print(await pick(3))
    print(await pick(1))


asyncio.run(drive())
