# A coroutine iterating a container FIELD off a monomorphized PARAM receiver
# (not off `self`): the field read binds through the shared record-or-
# container gate, so the loop iterates the parameter's own list. The element
# is appended AFTER the coroutine object exists and before it runs, so a frame
# that copied the field at construction would sum 3 where the alias sums 8.
import asyncio
from typing import Iterable
from tpy import int32, Own


class Summer[T: Iterable[int32]]:
    items: T

    def __init__(self, items: Own[T]) -> None:
        self.items = items


async def total(s: Summer[list[int32]]) -> int32:
    result = 0
    for x in s.items:  # tpyc: ok
        result += x
    return result


def main() -> None:
    xs = [1, 2]
    s = Summer(xs)
    c = total(s)
    s.items.append(5)
    print(asyncio.run(c))


main()
