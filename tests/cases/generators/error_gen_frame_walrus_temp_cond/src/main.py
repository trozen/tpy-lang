# The resumable-frame twin of error_gen_while_walrus_temp_cond: a two-yield
# generator lands on the frame, whose Branch condition flushes argument temps
# BEFORE the walrus store they may read, so the mix rejects there too.
from typing import Iterator


def total(*xs: int) -> int:
    n = 0
    for x in xs:
        n += x
    return n


# Two yields, so the peephole declines and the body renders as a frame.
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
