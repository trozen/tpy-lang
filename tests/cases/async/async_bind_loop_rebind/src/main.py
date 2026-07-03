# Same-coroutine rebind + consume inside a loop body (the retry shape):
# each iteration re-emplaces the concrete frame and awaits it.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    total = 0
    for i in range(3):
        c = add_one(i)
        total += await c
    print(total)


def main() -> None:
    asyncio.run(main_coro())


main()
