# Collection literals assigned to locals inside a coroutine body are hoisted
# into the resumable frame. Their deferred element-type inference (Pending*
# types) must be resolved before the frame-field type is rendered -- list,
# dict, set, and an annotated empty list, each used across an await.
import asyncio
from tpy import int32


async def w() -> None:
    nums = [1, 2, 3]
    d = {1: 10, 2: 20}
    s = {7, 8}
    empty: list[int32] = []
    await asyncio.sleep(0)
    for n in nums:
        print(n)
    print(len(d))
    print(len(s))
    print(len(empty))


def main() -> None:
    asyncio.run(w())


main()
