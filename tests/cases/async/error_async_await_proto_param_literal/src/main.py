# Awaiting a protocol-param coroutine with a collection-literal argument is
# rejected cleanly. The literal has no concrete C++ type at the call site (the
# param is a concept), so it can't be typed into the sub-future frame field nor
# kept alive across the suspension. A named iterable is the supported form
# (see async_await_proto_param). Full support is gated on the collection-
# literal-in-coro-body fix (BUGS.md).
import asyncio
from typing import Iterable


async def consume(it: Iterable[int]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    await consume([1, 2, 3])  # tpyc: error(/does not yet support a collection literal argument/)


def main() -> None:
    asyncio.run(main_coro())


main()
