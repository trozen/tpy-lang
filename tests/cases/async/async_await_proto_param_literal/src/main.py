# Awaiting a protocol-param coroutine with a collection-literal argument. The
# literal has no name at the call site, so the uniform frame-temp rule hoists
# it into a slot of the AWAITING coroutine's own frame; the sub-future's
# protocol field is then deduced off that named local and stays alive across
# the suspension. Pairs with async_await_proto_param (lvalue) and
# async_await_proto_param_rvalue (owned rvalue).
import asyncio
from typing import Iterable


async def consume(it: Iterable[int]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    # The subject: the literal is hoisted, not passed as a temporary.
    await consume([1, 2, 3])  # tpyc: ok


def main() -> None:
    asyncio.run(main_coro())


main()
