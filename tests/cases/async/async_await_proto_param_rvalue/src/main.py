# Awaiting a protocol-param coroutine with an rvalue (owned) argument:
# `await consume(make_it())` exercises the non-reference branch of the
# forwarding-deduction spelling -- the rvalue deduces to a value type, so the
# awaiting coroutine's sub-future OWNS the moved-in iterable (no borrow). Pairs
# with async_await_proto_param, which covers the lvalue (borrow) branch.
import asyncio
from typing import Iterable
from tpy import int32, Own


def make_it() -> Own[list[int32]]:
    xs: list[int32] = [1, 2, 3]
    return xs


async def consume(it: Iterable[int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    await consume(make_it())


def main() -> None:
    asyncio.run(main_coro())


main()
