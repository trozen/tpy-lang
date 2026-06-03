# Async sibling of error_gen_proto_param_aliased_local: a static-protocol
# coroutine param aliased into a local then iterated across an await has no
# concrete frame backing (only captured params carry the deduced template arg
# T_<pname>), so it is rejected cleanly at frame-field emit. The reject path is
# shared between generator and coroutine shapes; this guards the coroutine arm.
import asyncio
from typing import Iterable


async def consume(it: Iterable[int]) -> None:  # tpyc: error(/protocol type aliasing a protocol-typed parameter/)
    xs = it
    for x in xs:
        await asyncio.sleep(0)
        print(x)


def main() -> None:
    asyncio.run(consume([1, 2]))


main()
