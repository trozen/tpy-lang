# An await result bound by a walrus in a `while` condition to a tuple owning its
# elements is refused naming the walrus's variable (BUGS.md#await-bind-pointer-local-seam).
import asyncio

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


async def amk(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


async def co(c: bool) -> int32:
    while (p := (await amk(5) if c else mk(7)))[0].n > 6:  # tpyc: error(/binding 'p' to the result of an await is not yet supported here: 'p' keeps/)
        print(1)
    return p[0].n


def main() -> None:
    print(asyncio.run(co(True)))


main()
