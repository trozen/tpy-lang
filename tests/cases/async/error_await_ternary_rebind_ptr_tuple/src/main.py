# An await result bound to a rebound tuple through a conditional expression
# is refused as the plain await binding is, naming the program's variable
# rather than the temporary the conditional is evaluated into
# (BUGS.md#await-bind-pointer-local-seam).
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


async def co(c: bool) -> int32:
    p = make_owned(1)
    q = p
    p = (await mk_a(5)) if c else make_owned(7)  # tpyc: error(/binding 'p' to the result of an await is not yet supported here: 'p' keeps/)
    q[0].n += 100
    return p[0].n * 1000 + q[0].n


def main() -> None:
    print(asyncio.run(co(True)))


main()
