# Async sibling of generators/gen_ptr_local_rvalue_frame: rvalue writes
# into a pointer-form coro-frame local materialize in frame slots.
import asyncio
from typing import Optional
from tpy import Int32


class Point:
    def __init__(self, x: Int32) -> None:
        self.x = x


async def read_after_await() -> None:
    saved: Optional[Point] = Point(42)
    await asyncio.sleep(0)
    saved = Point(7)
    await asyncio.sleep(0)
    if saved is not None:
        print(saved.x)


def main() -> None:
    asyncio.run(read_after_await())


main()
