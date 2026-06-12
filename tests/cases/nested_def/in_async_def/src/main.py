# A nested def inside an async def is a plain sync function: its return
# must not lower to the coroutine's Poll-ready shape. (The def and its
# calls sit between suspension points -- using a nested def ACROSS an
# await is a separate unsupported shape, see BUGS.md.)
import asyncio


async def outer() -> int:
    await asyncio.sleep(0)

    def g() -> int:
        return 5

    return g()


def main() -> None:
    print(asyncio.run(outer()))


main()
