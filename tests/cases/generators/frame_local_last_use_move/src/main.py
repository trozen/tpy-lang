# A movable local at its last use inside a generator frame. The frame slot
# renders `(*buf)`, which must not be read as a materialized temporary -- the
# last-use move has to survive. Copy-vs-move is unobservable here (the source
# is dead after the append), so the pinned `std::move` in the snapshot is what
# this case guards. The second append is the contrast: `bytearray` and `bytes`
# are distinct types, so an owning `bytes` sink refuses a bytearray outright
# and the copy is WRITTEN -- `bytes(ba)`, which is never a move. The moved
# local is a `list[int32]` rather than the bytearray it used to be for the same
# reason: that pair no longer reaches the move arm at all. What the written
# copy does to the two objects is pinned in
# tests/cases/bytes/bytearray_copy_into_bytes_sink.
from typing import Iterator

from tpy import int32


def chunks(n: int) -> Iterator[int]:
    out: list[list[int32]] = []
    seen: list[bytes] = []
    buf = [1, 2, 3]
    ba = bytearray(b"abc")
    yield n
    out.append(buf)  # the frame slot, moved at its last use
    seen.append(bytes(ba))  # the written copy into a `bytes` element slot
    print(len(out), len(out[0]), len(seen[0]))


def main():
    for v in chunks(7):
        print(v)


main()
