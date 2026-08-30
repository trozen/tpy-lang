# A generator `while` condition that both binds a walrus and needs an argument
# temporary is rejected rather than emitted: the temp lives in the loop head,
# where it could run before the walrus assignment it reads.
from typing import Iterator


def total(*xs: int) -> int:
    n = 0
    for x in xs:
        n += x
    return n


def counted(limit: int) -> Iterator[int]:
    i = 0
    # The varargs call materializes an argument temp; the walrus binds in the
    # same condition. That combination is the rejected shape.
    while (i := i + 1) < total(limit, limit):  # tpyc: error(/walrus binding/)
        yield i


def main() -> None:
    for v in counted(2):
        print(v)


main()
