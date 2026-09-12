# A capture reusing the name of a frame-resident local must not strip that
# local's frame slot: `total` is assigned before the match, captured by an arm,
# and read after a later yield, so losing the slot breaks the generator.
from typing import Iterator

from tpy import int32


def run(n: int32) -> Iterator[int32]:
    total = 100
    yield total

    match n:
        case 5 as total:
            yield total
        case _:
            yield -1

    yield total + 1


def main() -> None:
    out = 0
    for v in run(5):
        out += v
    print(out)


main()
