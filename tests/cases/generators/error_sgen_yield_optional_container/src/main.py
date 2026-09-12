# The shape adjacent to yielding a container from a simple generator: an
# OPTIONAL container slot. Optional carries pointer/storage machinery the
# position-blind yield render does not spell, so it keeps its own rung.
from typing import Iterator, Optional
from tpy import int32


def maybe(xs: list[int32], n: int32) -> Iterator[Optional[list[int32]]]:  # tpyc: error(/not yet supported.*sgen.yield_type/)
    i = 0
    while i < n:
        yield xs
        i += 1


def main() -> None:
    for got in maybe([1], 1):
        if got is not None:
            print(len(got))


main()
