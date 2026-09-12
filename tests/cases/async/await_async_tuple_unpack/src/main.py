# CPython-parity companion to await_tuple_own_unpack: the same await-result
# tuple move-out, but via an ordinary `async def` returning a tuple (runs
# under CPython's asyncio); @nocopy guards the move, output checks parity.
import asyncio
from tpy import int32, Own, nocopy


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


async def make_pair() -> tuple[Own[Counter], int32]:
    return (Counter(10), 99)


async def main_coro() -> None:
    c, tag = await make_pair()
    c.bump()
    c.bump()
    print(c.n, tag)


def main() -> None:
    asyncio.run(main_coro())


main()
