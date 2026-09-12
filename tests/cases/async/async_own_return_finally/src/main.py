# An async Own[T] return of a local under try/finally: the finally reads
# (and mutates) the local, so the return must not move it early -- the
# deferred capture materializes after the finally, CPython-identical.
import asyncio
from tpy import int32, Own


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def make(v: int32) -> Own[Box]:
    b = Box(v)
    try:
        return b
    finally:
        b.v += 10
        print(b.v)


async def driver() -> int32:
    b = await make(7)
    return b.v


def main() -> None:
    print(asyncio.run(driver()))


main()
