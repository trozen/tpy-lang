# asyncio.run of a borrowing tuple coroutine is refused (LANGUAGE_FEATURES
# "direct-await-only"), advising `-> tuple[int32, Own[Box]]`.
import asyncio
from tpy import int32


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def pair(b: Box) -> tuple[int32, Box]:
    await asyncio.sleep(0)
    return (1, b)


def main() -> None:
    b = Box(3)
    t = asyncio.run(pair(b))  # tpyc: error(/borrow-returning coroutine.*'-> tuple\[int32, Own\[Box\]\]'/)
    print(t[0])


main()
