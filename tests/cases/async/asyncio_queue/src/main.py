# asyncio.Queue: bounded producer/consumer with backpressure (maxsize 2),
# join/task_done, the nowait paths + bounds, an unbounded queue, and the
# task_done() over-call guard. Cancelling a task parked in get / put / join
# delivers CancelledError there and the queue keeps serving the others; a
# putter or joiner woken but cancelled before it ran leaves the slot or the
# wake to the next one.
import asyncio
from asyncio import Queue, QueueEmpty, QueueFull
from tpy import int32


async def producer(q: Queue[int32]) -> None:
    i = 0
    while i < 5:
        await q.put(i)
        i += 1


async def consumer(q: Queue[int32], out: list[int32]) -> None:
    n = 0
    while n < 5:
        x = await q.get()
        out.append(x)
        q.task_done()
        n += 1


async def q_getter(tag: str, q: Queue[int32]) -> None:
    try:
        v = await q.get()
        print("cancel get:", tag, "got", v)
    except asyncio.CancelledError:
        print("cancel get:", tag, "cancelled")
        raise


async def q_putter(section: str, tag: str, q: Queue[int32],
                   v: int32) -> None:
    try:
        await q.put(v)
        print(section, tag, "put", v)
    except asyncio.CancelledError:
        print(section, tag, "cancelled")
        raise


async def reap(tag: str, t: asyncio.Task[None]) -> None:
    try:
        await t
        print(tag, "not cancelled (WRONG)")
    except asyncio.CancelledError:
        print(tag, "saw the cancel")


async def cancel_get() -> None:
    q: Queue[int32] = Queue(0)
    ta = asyncio.create_task(q_getter("a", q))
    tb = asyncio.create_task(q_getter("b", q))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- a leaves the getters, so the put below goes to b
    await reap("cancel get:", ta)
    q.put_nowait(1)
    await tb
    tc = asyncio.create_task(q_getter("c", q))
    td = asyncio.create_task(q_getter("d", q))
    await asyncio.sleep(0.01)
    q.put_nowait(2)
    tc.cancel()  # tpyc: ok -- woken by the put but cancelled before it ran: d gets the item
    await reap("cancel get:", tc)
    await td


async def cancel_put() -> None:
    q: Queue[int32] = Queue(1)
    q.put_nowait(0)
    ta = asyncio.create_task(q_putter("cancel put:", "a", q, 1))
    tb = asyncio.create_task(q_putter("cancel put:", "b", q, 2))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- a's item never enters the full queue
    await reap("cancel put:", ta)
    print("cancel put: got", q.get_nowait())
    await tb
    print("cancel put: got", q.get_nowait())


async def main_coro() -> None:
    q: Queue[int32] = Queue(2)
    print("empty:", q.empty(), "full:", q.full(), "qsize:", q.qsize(),
          "maxsize:", q.maxsize)
    out: list[int32] = []
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

    qb: Queue[int32] = Queue(1)
    qb.put_nowait(1)
    try:
        qb.put_nowait(2)
        print("no full error")
    except QueueFull:
        print("caught full")

    # Unbounded queue: never full, put_nowait never raises.
    qu: Queue[int32] = Queue(0)
    qu.put_nowait(1)
    qu.put_nowait(2)
    print("unbounded full:", qu.full(), "qsize:", qu.qsize())


async def q_joiner(tag: str, q: Queue[int32]) -> None:
    try:
        await q.join()
        print("handoff join:", tag, "joined")
    except asyncio.CancelledError:
        print("handoff join:", tag, "cancelled")
        raise


async def handoff() -> None:
    q: Queue[int32] = Queue(1)
    q.put_nowait(0)
    ta = asyncio.create_task(q_putter("handoff put:", "a", q, 1))
    tb = asyncio.create_task(q_putter("handoff put:", "b", q, 2))
    await asyncio.sleep(0.01)
    print("handoff put: got", q.get_nowait())
    ta.cancel()  # tpyc: ok -- a was woken by the get; the free slot goes to b
    await reap("handoff put:", ta)
    await tb
    print("handoff put: got", q.get_nowait())
    qj: Queue[int32] = Queue(0)
    qj.put_nowait(1)
    ja = asyncio.create_task(q_joiner("a", qj))
    jb = asyncio.create_task(q_joiner("b", qj))
    await asyncio.sleep(0.01)
    qj.get_nowait()
    qj.task_done()
    ja.cancel()  # tpyc: ok -- a was woken by task_done; b still joins
    await reap("handoff join:", ja)
    await jb


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(cancel_get())
    asyncio.run(cancel_put())
    asyncio.run(handoff())


main()
