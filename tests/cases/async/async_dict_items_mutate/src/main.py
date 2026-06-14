# Async coroutine iterating d.items() across awaits: the unpacked value
# var aliases the dict's stored object across suspensions, so mutations
# after the await reach the dict.
from tpy import Int32
import asyncio


class C:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


async def bump(d: dict[Int32, C]) -> Int32:
    n = 0
    for k, c in d.items():
        await asyncio.sleep(0)
        c.v = c.v + 1
        n = n + k
    return n


def main():
    d = {1: C(10), 2: C(20)}
    print(asyncio.run(bump(d)))
    print(d[1].v, d[2].v)


main()
