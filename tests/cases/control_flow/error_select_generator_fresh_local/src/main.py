# An all-fresh select of generator frames at a local is refused, not a crash:
# the frame type has no prvalue slot spelling (BUGS.md#reference-ternary-position-gaps).
from typing import Iterator
from tpy import int32


def gen(n: int32) -> Iterator[int32]:
    yield n


def run(c: bool) -> None:
    g = gen(1) if c else gen(10)  # tpyc: error(/decl\.slot_type/)
    for v in g:
        print(v)


run(True)
