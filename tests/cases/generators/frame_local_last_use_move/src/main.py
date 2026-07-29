# A movable local at its last use inside a generator frame. The frame slot
# renders `(*buf)`, which must not be read as a materialized temporary -- the
# last-use move has to survive. Copy-vs-move is unobservable here (the source
# is dead after the append), so the pinned `std::move` in the snapshot is what
# this case guards.
from typing import Iterator


def chunks(n: int) -> Iterator[int]:
    out: list[bytes] = []
    buf = bytearray(b"abc")
    yield n
    out.append(buf)
    print(len(out), len(out[0]))


def main():
    for v in chunks(7):
        print(v)


main()
