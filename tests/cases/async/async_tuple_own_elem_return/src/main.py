# Async value-tuple return of a tuple LITERAL with an Own-element NAME
# source: the render must move the local into the slot (regression: the
# untargeted render copied bare -- a compile error for this @nocopy
# record). Mutation before the return and after the await proves the
# moved object carries state across the boundary.
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
    c = Counter(10)
    c.bump()
    return (c, 99)


async def main_coro() -> None:
    c, tag = await make_pair()
    c.bump()
    print(c.n, tag)


def main() -> None:
    asyncio.run(main_coro())


main()
