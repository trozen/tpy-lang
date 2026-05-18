# Nesting two `await`-in-`finally` regions is deferred (the inner
# AsyncFinallyExit would need to forward a pending exception or
# pending return to the outer slot when it itself is inside an
# outer CFG-based finally region).
import asyncio


async def sub() -> None:
    pass


async def caller() -> None:
    try:
        try:  # tpyc: error(/nesting two `await`-in-`finally`/)
            await sub()
        finally:
            await sub()
    finally:
        await sub()


def main() -> None:
    asyncio.run(caller())


main()
