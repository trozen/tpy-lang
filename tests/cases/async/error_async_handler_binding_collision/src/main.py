# An `except ... as e` binding whose name is already a frame field: the flat
# frame scope would mistype one of the two, so this rejects.
import asyncio
from tpy import Int32


async def step(n: Int32) -> Int32:
    return n + 1


async def f(n: Int32) -> Int32:  # tpyc: error(/res\.handler_binding/)
    e = n
    try:
        n = await step(n)
    # `e` is already a frame field above.
    except ValueError as e:
        print(e)
    return n


def main() -> None:
    print(asyncio.run(f(1)))


main()
