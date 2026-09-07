# `asyncio.run(w.go())` -- a MEMBER async-method factory as the driver argument:
# the coroutine factory spells inline inside the adapter.
from tpy import Int32
import asyncio


class Worker:
    n: Int32

    def __init__(self) -> None:
        self.n = 1

    async def go(self) -> None:
        self.n += 1
        print(self.n)


def main() -> None:
    w = Worker()
    asyncio.run(w.go())  # the member coroutine factory at the driver slot
    print(w.n)


main()
