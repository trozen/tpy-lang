# asyncio.run cannot be called from a running event loop. Per
# docs/ASYNC_DESIGN.md ("Context propagation"): mirrors CPython's
# RuntimeError. Validates the runtime panic path.
import asyncio


async def inner() -> None:
    print("inner start")


async def outer() -> None:
    asyncio.run(inner())  # second run while one is already active


def main() -> None:
    asyncio.run(outer())


main()
