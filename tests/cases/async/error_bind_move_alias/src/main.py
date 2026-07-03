# Binding from a name moves the handle out of the source; using the source
# afterwards is rejected (CPython aliases coroutine objects and fails at
# runtime on the second await -- TPy moves and rejects at compile time;
# declared divergence).
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(1)
    d = c  # tpyc: error(/moves the coroutine out of 'c'/)
    print(await d)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
