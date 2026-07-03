# Diagnostics for unconsumed bound coroutines: a handle never consumed warns
# at compile time (runtime: destroyed without running -- CPython's "never
# awaited" RuntimeWarning goes to stderr there); rebinding over an unconsumed
# handle warns. Output proves the dropped coroutines never ran.
import asyncio


async def note(tag: str) -> None:
    print("ran", tag)


async def main_coro() -> None:
    c = note("dropped")  # tpyc: warning(/never consumed/)
    d = note("first")
    d = note("second")  # tpyc: warning(/drops the previous coroutine/)
    await d
    await asyncio.sleep(0.001)


def main() -> None:
    asyncio.run(main_coro())


main()
