# asyncio.run of a generic tuple payload is refused at a reference T, where it
# borrows (LANGUAGE_FEATURES "direct-await-only").
import asyncio
from tpy import int32


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def solo[T](x: T) -> tuple[T, int32]:
    await asyncio.sleep(0)
    return (x, 1)


def main() -> None:
    b = Box(3)
    t = asyncio.run(solo(b))  # tpyc: error(/borrow-returning coroutine.*'-> tuple\[Own\[Box\], int32\]'/)
    print(t[1])


main()
