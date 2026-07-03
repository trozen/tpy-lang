# Rebinding a coroutine handle drops the previous coroutine unrun (warns;
# CPython emits a stderr RuntimeWarning at GC); the unique_ptr reassignment
# works in both async (frame field) and sync (plain local) contexts.
import asyncio


async def value(n: int) -> int:
    return n


async def main_coro() -> None:
    c = value(1)
    c = value(2)  # tpyc: warning(/drops the previous coroutine/)
    print(await c)


def main() -> None:
    d = value(3)
    d = value(4)  # tpyc: warning(/drops the previous coroutine/)
    asyncio.run(main_coro())
    print(asyncio.run(d))


main()
