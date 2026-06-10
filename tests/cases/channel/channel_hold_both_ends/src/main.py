# Both ends of a channel held in one coroutine across awaits: tx, rx must
# frame-store both Own handles to survive the send()/recv() suspensions
# (Sender/Receiver are @nocopy, so a silent copy at the unpack would fail).
import asyncio
from tpy.channel import channel, ChannelClosed


async def roundtrip() -> int:
    tx, rx = channel[int](4)
    await tx.send(7)
    await tx.send(35)
    tx.close()
    total = 0
    while True:
        try:
            total += await rx.recv()
        except ChannelClosed:
            break
    return total


def main() -> None:
    print(asyncio.run(roundtrip()))


main()
