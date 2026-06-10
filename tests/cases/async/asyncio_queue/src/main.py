# asyncio.Queue: bounded producer/consumer with backpressure (maxsize 2),
# join/task_done, the nowait paths + bounds, an unbounded queue, and the
# task_done() over-call guard.
import asyncio
from asyncio import Queue, QueueEmpty, QueueFull
from tpy import Int32


async def producer(q: Queue[Int32]) -> None:
    i = 0
    while i < 5:
        await q.put(i)
        i += 1


async def consumer(q: Queue[Int32], out: list[Int32]) -> None:
    n = 0
    while n < 5:
        x = await q.get()
        out.append(x)
        q.task_done()
        n += 1


async def main_coro() -> None:
    q: Queue[Int32] = Queue(2)
    print("empty:", q.empty(), "full:", q.full(), "qsize:", q.qsize(),
          "maxsize:", q.maxsize)
    out: list[Int32] = []
    pt = asyncio.create_task(producer(q))
    ct = asyncio.create_task(consumer(q, out))
    await q.join()
    await asyncio.gather(pt, ct)
    print("got:", out)
    # All work drained -- join() now returns on its first poll.
    await q.join()
    print("joined again")
    try:
        q.task_done()
        print("no task_done error")
    except ValueError:
        print("caught task_done")

    q.put_nowait(99)
    print("get_nowait:", q.get_nowait())
    try:
        q.get_nowait()
        print("no empty error")
    except QueueEmpty:
        print("caught empty")

    qb: Queue[Int32] = Queue(1)
    qb.put_nowait(1)
    try:
        qb.put_nowait(2)
        print("no full error")
    except QueueFull:
        print("caught full")

    # Unbounded queue: never full, put_nowait never raises.
    qu: Queue[Int32] = Queue(0)
    qu.put_nowait(1)
    qu.put_nowait(2)
    print("unbounded full:", qu.full(), "qsize:", qu.qsize())


def main() -> None:
    asyncio.run(main_coro())


main()
