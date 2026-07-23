# A borrow-returning coroutine into a structural Awaitable[T]-typed slot
# (poll_once): the templated consumer expects an owned Poll<T> payload, so
# the borrow form is rejected like the Own[Cancellable[T]] consumers.
import asyncio
from tpy.coro import poll_once


class C:
    v: int

    def __init__(self) -> None:
        self.v = 1

    async def get(self) -> "C":
        await asyncio.sleep(0)
        return self


def main() -> None:
    c = C()
    p = poll_once(c.get())  # tpyc: error(/borrow-returning coroutine/)
    print(p.is_ready())


main()
