# A `with ... as t` target read across a suspension that comes AFTER the
# statement. The `with` body itself never suspends, so the target's frame
# residency cannot depend on the body containing a yield -- the target is an
# ordinary function local and outlives its statement.
from typing import Iterator

from tpy import Int32


class Counter:
    def __init__(self, start: Int32):
        self.n = start

    def __enter__(self) -> "Counter":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def steps(limit: Int32) -> Iterator[Int32]:
    c = Counter(limit)
    with c as guard:
        pass
    yield 1
    # Mutating here too: the target still aliases `c` after the block.
    guard.n += 1
    yield guard.n
    yield c.n


def main() -> None:
    for v in steps(5):
        print(v)


main()
