# Resumable-path generator with a reference-type (list) parameter,
# captured into the coro frame by reference (`T&`) -- same capture shape
# as the legacy generator path. Flat body (no loop) so it routes through
# the resumable emitter. (Phase D: params widening.)
from typing import Iterator
from tpy import Int32


def first_two(xs: list[Int32]) -> Iterator[Int32]:
    yield xs[0]
    yield xs[1]


def main() -> None:
    data = [10, 20, 30]
    for v in first_two(data):
        print(v)


main()
