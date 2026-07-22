# A nested def inside an async def is a plain sync function emitted as a
# frame member: its return must not lower to the coroutine's Poll-ready
# shape.
import asyncio


async def outer() -> int:
    await asyncio.sleep(0)

    def g() -> int:
        return 5

    return g()


def main() -> None:
    print(asyncio.run(outer()))


main()
