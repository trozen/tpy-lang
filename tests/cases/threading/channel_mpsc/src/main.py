# Blocking MPSC channel fan-in over real OS threads. Two producer threads each
# hold a cloned Sender and push Items through a capacity-2 ring (small enough
# to force send() to block while full); main holds NO sender, so when both
# producers finish and their Sender fields drop, the last drop auto-closes the
# channel and ends `for item in rx:`. The @nocopy Item payload forces
# move-through -- a silent copy at send/recv would be a compile error -- so
# this proves the value MOVES across the thread boundary, not copies.
from tpy import int32, Own, nocopy
from tpy.thread import spawn
from tplib.channel import channel, Sender, Receiver


@nocopy
class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class Producer:
    tx: Sender[Item]
    base: int32

    def __init__(self, tx: Own[Sender[Item]], base: int32) -> None:
        self.tx = tx
        self.base = base

    def run(self) -> None:
        i = 0
        while i < 3:
            self.tx.send(Item(self.base + i))
            i += 1


def main() -> None:
    tx, rx = channel[Item](2)
    h2 = spawn(Producer(tx.clone(), 100))
    h1 = spawn(Producer(tx, 0))
    total = 0
    count = 0
    for item in rx:
        total += item.v
        count += 1
    h1.join()
    h2.join()
    print(count)
    print(total)


main()
