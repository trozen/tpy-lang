# The inverse of await_bind_move: an await-bound local used AGAIN after a sink
# must NOT be moved at the earlier use. The payload owns a list, so a wrong
# move is observable -- a moved-from vector is left empty, and the second read
# would see 0 instead of 3.
import asyncio
from tpy import Own, Int32


class Payload:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


async def make() -> Own[Payload]:
    return Payload()


def size_of(p: Own[Payload]) -> Int32:
    return len(p.items)


async def used_again() -> Int32:
    p = await make()
    # NOT the last use of p, so this must copy rather than move -- the copy
    # is what the warning names, and what keeps p.items intact for the read
    # below. A wrong move here would empty the vector and give 3 + 0.
    first = size_of(p)  # tpyc: warning(/copies Payload into owned storage/)
    return first + len(p.items)


async def main_coro() -> None:
    print(await used_again())


def main() -> None:
    asyncio.run(main_coro())


main()
