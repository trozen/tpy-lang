# A `with` target's resumable-frame storage is owned by the `with` machinery, so
# the frame carries exactly one slot for it, not two. The target lives across a
# yield and is mutated between yields, so a lost or duplicated slot shows up as a
# wrong value. The name deliberately does NOT collide with any class.
from typing import Iterator

from tpy import int32


class Counter:
    def __init__(self, start: int32):
        self.n = start

    def __enter__(self) -> "Counter":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def steps(limit: int32) -> Iterator[int32]:
    c = Counter(limit)
    with c as guard:
        yield guard.n
        guard.n += 1
        yield guard.n


def main() -> None:
    total = 0
    for v in steps(5):
        total += v
    print(total)


main()
