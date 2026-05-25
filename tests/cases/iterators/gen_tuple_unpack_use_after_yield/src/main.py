# Regression: a tuple-unpack for-loop (`for a, b in pairs:`) where an unpack
# target is read AFTER a `yield` in the same iteration. The resumable for-loop
# emit binds only the synthetic `__for_tup` loop var into the frame, not the
# destructured targets, so on that path `b` would be a stale frame field after
# resume (printed garbage). The eligibility gate keeps tuple-unpack generators
# on the legacy path, which binds the targets as frame-field assignments so
# they persist across the yield.
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
