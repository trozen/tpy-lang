# A @nocopy Box local live across a yield is frame-promoted; appending it to a
# list moves out of the frame slot at its last use rather than copying.
from tplib.box import Box
from tpy import Int32
from typing import Iterator


def collect() -> Iterator[Int32]:
    boxes: list[Box[Int32]] = []
    a = Box(7)
    yield 0
    boxes.append(a)  # tpyc: ok
    yield len(boxes)


def main() -> None:
    for v in collect():
        print(v)


main()
