# Producer/consumer over a capacity-2 Channel[Counter]: send blocks when the
# buffer is full, recv blocks when empty, explicit close() ends the stream.
# The @nocopy payload forces move-through-channel -- a silent copy at send or
# recv would be a compile error. A task cancelled while parked in recv /
# send raises CancelledError there and the channel keeps working; a recv
# woken by a send but cancelled before it ran leaves the value queued.
import asyncio
from tpy import int32, Own, nocopy
from tpy.channel import channel, Sender, Receiver, ChannelClosed


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def producer(tx: Own[Sender[Counter]]) -> None:
    i = 0
    while i < 5:
        await tx.send(Counter(i))
        i += 1
    tx.close()


async def consumer(rx: Own[Receiver[Counter]]) -> None:
    while True:
        try:
            c = await rx.recv()
            print(c.n)
        except ChannelClosed:
            break


async def main_co() -> None:
    tx, rx = channel[Counter](2)
    p = asyncio.create_task(producer(tx))
    c = asyncio.create_task(consumer(rx))
    await p
    await c


async def cancel_consumer(rx: Own[Receiver[Counter]]) -> None:
    try:
        c = await rx.recv()
        print("cancel recv: got (WRONG)", c.n)
    except asyncio.CancelledError:
        print("cancel recv: cancelled")
    c2 = await rx.recv()
    print("cancel recv: got", c2.n)


async def cancel_producer(tx: Own[Sender[Counter]]) -> None:
    try:
        await tx.send(Counter(2))
        print("cancel send: sent (WRONG)")
    except asyncio.CancelledError:
        print("cancel send: cancelled")
    await tx.send(Counter(3))


async def cancel_co() -> None:
    tx, rx = channel[Counter](1)
    t = asyncio.create_task(cancel_consumer(rx))
    await asyncio.sleep(0.01)
    t.cancel()  # tpyc: ok -- the parked recv raises; the next recv gets the value
    await asyncio.sleep(0.01)
    await tx.send(Counter(7))
    await t
    tx2, rx2 = channel[Counter](1)
    await tx2.send(Counter(1))
    t2 = asyncio.create_task(cancel_producer(tx2))
    await asyncio.sleep(0.01)
    t2.cancel()  # tpyc: ok -- the parked send raises without pushing its value
    await asyncio.sleep(0.01)
    first = await rx2.recv()
    print("cancel send: got", first.n)
    second = await rx2.recv()
    print("cancel send: got", second.n)
    await t2


async def woken_consumer(rx: Own[Receiver[Counter]]) -> None:
    try:
        c = await rx.recv()
        print("woken recv: got (WRONG)", c.n)
    except asyncio.CancelledError:
        print("woken recv: cancelled")
    c2 = await rx.recv()
    print("woken recv: got", c2.n)


async def woken_co() -> None:
    tx, rx = channel[Counter](1)
    t = asyncio.create_task(woken_consumer(rx))
    await asyncio.sleep(0.01)
    await tx.send(Counter(8))
    t.cancel()  # tpyc: ok -- woken by the send, cancelled before it ran: 8 stays queued
    await t


asyncio.run(main_co())
asyncio.run(cancel_co())
asyncio.run(woken_co())
