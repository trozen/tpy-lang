# An async-factory call bound to a local holds the CONCRETE frame in an optional
# slot, and every rebind re-emplaces into that slot -- a call source constructs
# in place, a name source moves in and clears the source.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


def main() -> None:
    c = add_one(41)
    c = add_one(1)  # tpyc: warning(/drops the previous coroutine/)
    d = add_one(7)
    c = d  # tpyc: warning(/drops the previous coroutine/)
    print(asyncio.run(c))


main()
