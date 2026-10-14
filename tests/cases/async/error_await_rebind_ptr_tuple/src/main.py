# An await result bound to a rebound tuple that owns an element is refused:
# the resume binds the result whole, where the tuple keeps each owned element
# in storage of its own (BUGS.md#await-bind-pointer-local-seam).
import asyncio

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_owned(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


async def mk_a(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


async def co() -> int32:
    p = make_owned(1)
    q = p[1].n
    p = await mk_a(5)  # tpyc: error(/binding 'p' to the result of an await is not yet supported here/)
    p[0].n += 100
    return p[0].n * 1000 + q


def main() -> None:
    print(asyncio.run(co()))


main()
