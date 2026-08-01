# An await-bound local is auto-moved at its last use, like any other owned
# local. @nocopy forces the issue: a copy at any of these sinks is a C++
# build error, so a passing run proves the move happened.
import asyncio
from tpy import Own, nocopy, Int32


@nocopy
class Payload:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    item: Payload

    def __init__(self, item: Own[Payload]) -> None:
        self.item = item


async def make(n: Int32) -> Own[Payload]:
    return Payload(n)


def take(p: Own[Payload]) -> Int32:
    return p.n


async def into_container() -> Int32:
    out: list[Payload] = []
    p = await make(1)
    out.append(p)                 # last use -> moves into the element slot
    return out[0].n


async def into_call_arg() -> Int32:
    p = await make(2)
    return take(p)                # last use -> moves into the Own param


async def into_field() -> Int32:
    p = await make(3)
    h = Holder(p)                 # last use -> moves into the field
    return h.item.n


async def in_a_loop() -> Int32:
    out: list[Payload] = []
    i = 0
    while i < 3:
        # Re-assigns the same frame field each iteration; moving out of it is
        # safe because the next read is preceded by this write.
        p = await make(i)
        out.append(p)
        i += 1
    return out[0].n + out[1].n + out[2].n


async def from_return() -> Own[Payload]:
    p = await make(4)
    return p                      # last use -> moves into the return slot


async def main_coro() -> None:
    print(await into_container())
    print(await into_call_arg())
    print(await into_field())
    print(await in_a_loop())
    r = await from_return()
    print(r.n)


def main() -> None:
    asyncio.run(main_coro())


main()
