# Future[None] -- void-payload completion signal. Regression guard for
# BUGS.md "Future[None] has no usable completion API": Own[None] used
# to lower to `void&&` and `_result: UninitArrayStorage[T, 1]` to
# `UninitArrayStorage<void, 1>`, both ill-formed. After the position-
# aware None resolution fix, Future[None] lowers to Future<std::monostate>
# and set_result(None) Just Works.
import asyncio
from asyncio import Future


async def producer(fut: Future[None]) -> None:
    fut.set_result(None)


async def main_coro() -> None:
    fut: Future[None] = Future[None]()
    asyncio.create_task(producer(fut))
    await fut
    print("done")


def main() -> None:
    asyncio.run(main_coro())


main()
