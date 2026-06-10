# Producer/consumer over a capacity-2 Channel[Counter]: send blocks when the
# buffer is full, recv blocks when empty, explicit close() ends the stream.
# The @nocopy payload forces move-through-channel -- a silent copy at send or
# recv would be a compile error.
import asyncio
from tpy import Int32, Own, nocopy
from tpy.channel import channel, Sender, Receiver, ChannelClosed


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
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


asyncio.run(main_co())
