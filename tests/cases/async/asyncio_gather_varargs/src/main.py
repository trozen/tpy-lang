# asyncio.gather (variadic-positional form): homogeneous Task[T] args
# collected into list[T] result. Covers multi-arg, single-arg, and
# *unpack call shapes -- all sharing the same `_GatherFuture[T]`
# engine as gather_list. The empty case (n == 0) is covered separately
# by `asyncio_gather_empty` (which uses gather_list).
import asyncio
from tpy import int32


async def fetch(n: int32) -> int32:
    await asyncio.sleep(0.001)
    return n * 2


async def main_coro() -> None:
    # Multi-arg: three positional tasks.
    t1 = asyncio.create_task(fetch(1))
    t2 = asyncio.create_task(fetch(2))
    t3 = asyncio.create_task(fetch(3))
    multi_results = await asyncio.gather(t1, t2, t3)
    print("multi:")
    for r in multi_results:
        print(r)

    # Single-arg.
    s1 = asyncio.create_task(fetch(10))
    single_results = await asyncio.gather(s1)
    print("single:")
    for r in single_results:
        print(r)

    # *unpack: build a list of tasks and unpack it at the call site.
    pending: list[asyncio.Task[int32]] = []
    pending.append(asyncio.create_task(fetch(4)))
    pending.append(asyncio.create_task(fetch(5)))
    unpacked_results = await asyncio.gather(*pending)
    print("unpacked:")
    for r in unpacked_results:
        print(r)


def main() -> None:
    asyncio.run(main_coro())


main()
