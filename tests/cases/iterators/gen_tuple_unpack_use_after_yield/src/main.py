# Regression: a tuple-unpack for-loop (`for a, b in pairs:`) where an unpack
# target is read AFTER a `yield` in the same iteration. The destructured
# targets are frame-stored (not emitted as ordinary C++ locals), so `b`
# survives the yield instead of reading a stale slot -- a naive resumable
# emit that bound only the synthetic `__for_tup` loop var printed garbage.
from typing import Iterator
from tpy import Int32


def gen(pairs: list[tuple[Int32, Int32]]) -> Iterator[Int32]:
    total = 0
    for a, b in pairs:
        total += a
        yield a + b
        total += b      # `b` must survive the yield above
    yield total


def main() -> None:
    for v in gen([(1, 2), (3, 4)]):
        print(v)


main()
