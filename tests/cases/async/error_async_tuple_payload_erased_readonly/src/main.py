# asyncio.run of a tuple payload borrowing a readonly element is refused; the
# advice wraps the element without its readonly: `-> tuple[int32, Own[Box]]`.
import asyncio
from tpy import int32, readonly


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def pair(b: readonly[Box]) -> tuple[int32, readonly[Box]]:
    await asyncio.sleep(0)
    return (1, b)


def main() -> None:
    b = Box(3)
    t = asyncio.run(pair(b))  # tpyc: error(/'-> tuple\[int32, Own\[Box\]\]'/)
    print(t[0])


main()
