# A match capture read across a yield needs frame residency (the arm bodies
# become separate resumable states), so the capture must keep its frame slot
# rather than being resolution-only. The name deliberately does NOT collide
# with any class.
from typing import Iterator

from tpy import Int32


def classify(n: Int32) -> Iterator[Int32]:
    match n:
        case 5 as hit:
            yield hit
            yield hit + 1
        case _:
            yield -1


def main() -> None:
    total = 0
    for v in classify(5):
        total += v
    print(total)


main()
