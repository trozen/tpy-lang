# Async sibling of error_gen_proto_param_realiased_local: a reassigned
# protocol-param alias is not a single-assignment forward, so it has no
# concrete frame backing and is rejected cleanly at frame emit. Guards the
# single-assignment gate on the coroutine arm (an async def is always the
# resumable path). The single-assignment form is supported -- see
# async_proto_param_aliased_local.
import asyncio
from typing import Iterable


async def consume(it: Iterable[int], it2: Iterable[int]) -> None:  # tpyc: error(/protocol type aliasing a protocol-typed parameter/)
    xs = it
    xs = it2
    for x in xs:
        await asyncio.sleep(0)
        print(x)


def main() -> None:
    asyncio.run(consume([1, 2], [3, 4]))


main()
