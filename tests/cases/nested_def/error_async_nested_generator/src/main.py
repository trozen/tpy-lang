# The nested-generator rejection also fires inside an async outer (the
# resumable frame-member path must never see a yield-containing nested def).
import asyncio
from typing import Iterator
from tpy import Int32


async def outer(k: Int32) -> Int32:
    def gen() -> Iterator[Int32]:  # tpyc: error(/nested generator functions are not supported/)
        yield k

    total = 0
    for v in gen():
        total += v
    return total


def main() -> None:
    print(asyncio.run(outer(4)))


main()
