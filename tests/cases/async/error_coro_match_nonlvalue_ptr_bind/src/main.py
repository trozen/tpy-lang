# Error: the async spelling of the generator reject -- binding a pointer-repr
# `Optional` field from a NON-lvalue match subject (a constructed temporary)
# inside a coroutine aliases the dispatch-local subject copy, which dangles
# across the suspension. Workaround: bind the subject to a local first.
import asyncio
from typing import Optional


class Inner:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Box:
    maybe: Optional[Inner]

    def __init__(self, m: Optional[Inner]) -> None:
        self.maybe = m


async def co() -> int:
    match Box(Inner(7)):  # tpyc: error(/would dangle across a suspension/)
        case Box(maybe=v):
            await asyncio.sleep(0)
            if v is not None:
                return v.n
    return 0


def main() -> None:
    print(asyncio.run(co()))


main()
