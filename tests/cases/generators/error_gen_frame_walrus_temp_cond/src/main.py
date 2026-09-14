# A generator `while` head mixing a walrus with an argument temp: the frame's
# Branch condition flushes argument temps BEFORE the walrus store they may
# read, so the mix is rejected.
from typing import Iterator


def total(*xs: int) -> int:
    n = 0
    for x in xs:
        n += x
    return n


# Two yields; the body renders as a resumable frame like every generator.
def counted(limit: int) -> Iterator[int]:  # tpyc: error(/res\.cond:cond\.mixed_walrus_temps/)
    i = 0
    yield 0
    # The varargs call materializes an argument temp; the walrus binds in the
    # same condition. That combination is the rejected shape.
    while (i := i + 1) < total(limit, limit):
        yield i


def main() -> None:
    for v in counted(2):
        print(v)


main()
