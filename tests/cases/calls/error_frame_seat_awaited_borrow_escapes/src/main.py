# A borrow of a fresh argument an awaited frame keeps, carried out of the
# coroutine by its return: the frame's seat dies with the coroutine, so the
# return is refused (docs/LANGUAGE_FEATURES.md "Owned Return Values").
import asyncio
from tpy import int32


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def solo[T](x: T) -> tuple[T, int32]:
    await asyncio.sleep(0)
    return (x, 1)


async def leak() -> Box:
    # the element borrows the temporary the awaited call was given
    return (await solo(Box(99)))[0]  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(asyncio.run(leak()).v)


main()
