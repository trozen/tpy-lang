# An `Own[Enum]` argument at an await position: the enum payload is the one
# value family the Own-argument row leaves unmodelled, so this rejects.
import asyncio
from enum import Enum
from tpy import Own


class Color(Enum):
    RED = 1
    BLUE = 2


def show(c: Color) -> None:
    print(c)


async def sink(c: Own[Color]) -> None:
    await asyncio.sleep(0.001)
    show(c)


async def go(k: Color) -> None:  # tpyc: error(/res\.await_param_type/)
    # The Own enum argument sits at the await.
    await sink(k)


def main() -> None:
    asyncio.run(go(Color.RED))


main()
