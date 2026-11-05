# A nested def in an async def lives on the frame, so a lambda that carries
# it out of the coroutine is refused as handing the def out directly is.
import asyncio
from typing import Callable


async def make() -> Callable[[], float]:
    x = 7.0

    def h() -> float:
        return x
    return lambda: h()  # tpyc: error(/nested function 'h' defined in an async function cannot escape/)


async def main() -> None:
    f = await make()
    print(f())


asyncio.run(main())
