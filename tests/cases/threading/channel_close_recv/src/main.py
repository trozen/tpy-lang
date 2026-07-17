# Explicit close() then drain: recv() returns the buffered items, and once the
# channel is closed AND empty the next recv() raises ChannelClosed. Exercises
# Sender.close() (force-close) and the explicit recv()/ChannelClosed path that
# the generator __iter__ otherwise swallows. Single-threaded (no blocking), so
# deterministic.
from tpy import Int32
from tplib.channel import channel, ChannelClosed


def main() -> None:
    tx, rx = channel[Int32](4)
    tx.send(10)
    tx.send(20)
    tx.close()
    total = 0
    total += rx.recv()
    total += rx.recv()
    print(total)
    try:
        rx.recv()
        print("no raise")
    except ChannelClosed:
        print("closed")


main()
