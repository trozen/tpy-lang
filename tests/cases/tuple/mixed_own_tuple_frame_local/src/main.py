# A mixed owned+borrow tuple local that lives in a RESUMABLE FRAME -- a
# multi-yield generator and an async function -- keeps the mixed render in its
# frame field. A whole-tuple storage field would copy the borrowed element, so
# both bodies write through it and the caller reads the original.
import asyncio
from typing import Iterator

from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def gen(b: Box) -> Iterator[Int32]:
    p = make_mixed(b)
    p[1].val = 88
    yield p[0].val
    yield p[1].val


async def coro(b: Box) -> Int32:
    p = make_mixed(b)
    p[1].val = 99
    await asyncio.sleep(0)
    return p[0].val + p[1].val


def main() -> None:
    b = Box(7)
    for v in gen(b):
        print("gen:", v)
    print("after gen:", b.val)

    c = Box(7)
    print("coro:", asyncio.run(coro(c)))
    print("after coro:", c.val)


main()
