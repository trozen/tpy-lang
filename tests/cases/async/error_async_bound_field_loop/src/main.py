# A coroutine iterating a field off a monomorphized PARAM receiver: the leaf
# gate admits self-field iterables only, so this rejects.
import asyncio
from typing import Iterable
from tpy import int32, Own


class Summer[T: Iterable[int32]]:
    items: T

    def __init__(self, items: Own[T]) -> None:
        self.items = items


async def total(s: Summer[list[int32]]) -> int32:
    result = 0
    # The iterable is a bound field off a parameter, not off `self`.
    for x in s.items:  # tpyc: error(/stmt\.for_each:field\.result_type/)
        result += x
    return result


def main() -> None:
    xs = [1, 2]
    print(asyncio.run(total(Summer(xs))))


main()
