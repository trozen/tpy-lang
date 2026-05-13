# Nested asyncio.run raises RuntimeError (mirrors CPython's "asyncio.run()
# cannot be called from a running event loop"). This case verifies the
# *uncaught* path: the exception is not caught, so the program terminates
# via tpy_terminate_handler. Companion `run_reentry_caught` covers the
# catchable-exception path. Per docs/ASYNC_DESIGN.md "Context propagation".
import asyncio


async def inner() -> None:
    print("inner start")


async def outer() -> None:
    asyncio.run(inner())  # second run while one is already active


def main() -> None:
    asyncio.run(outer())


main()
