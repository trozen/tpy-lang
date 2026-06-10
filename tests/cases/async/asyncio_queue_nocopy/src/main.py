# Queue of a @nocopy element (Box): get must MOVE the element out of the
# buffer, not copy it (guards the Own[T] move-out path for reference T).
import asyncio
from asyncio import Queue
from tplib import Box
from tpy import Int32


async def main_coro() -> None:
    q: Queue[Box[Int32]] = Queue(0)
    await q.put(Box(Int32(10)))
    q.put_nowait(Box(Int32(20)))
    a = await q.get()
    b = q.get_nowait()
    print(a.get(), b.get())


def main() -> None:
    asyncio.run(main_coro())


main()
