# Awaiting a protocol-param coroutine with a collection-literal argument: the
# literal is held in a slot of the AWAITING coroutine's frame, which the
# sub-coroutine borrows across the suspension. Pairs with
# async_await_proto_param (lvalue) and async_await_proto_param_rvalue (owned).
import asyncio
from typing import Iterable


async def consume(it: Iterable[int]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    # The subject: the awaiting frame holds the literal; the callee borrows it.
    await consume([1, 2, 3])  # tpyc: ok


def main() -> None:
    asyncio.run(main_coro())


main()
