# asyncio.gather (variadic-positional form): homogeneous Task[T] args
# collected into list[T] result. Covers multi-arg and single-arg
# shapes -- both sharing the same `_GatherFuture[T]` engine as
# gather_list. The empty case (n == 0) is covered separately by
# `asyncio_gather_empty` (which uses gather_list). The `*unpack` form
# (`gather(*list_of_tasks)`) is currently blocked by a sema dispatch
# gap on TpyStarUnpack into generic reference-element varargs (see
# BUGS.md); users with a list in hand call `gather_list(tasks)`
# instead.
import asyncio
from tpy import Int32


async def fetch(n: Int32) -> Int32:
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


def main() -> None:
    asyncio.run(main_coro())


main()
