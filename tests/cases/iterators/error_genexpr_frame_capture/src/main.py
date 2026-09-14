# A genexpr inside a resumable frame reading a container LOCAL of the frame (a
# `frame_slot` field; a param is a plain field and works) rejects: the capture
# helper spells plain frame fields only. The reject is unlocated (def line).
from tpy import int32
from typing import Iterator


def walk(src: list[int32]) -> Iterator[int32]:  # tpyc: error(/genexpr.frame_capture/)
    xs = [x for x in src]
    yield 0
    xs = [x + 1 for x in xs]
    yield sum(y * 2 for y in xs)


def main() -> None:
    print(list(walk([1, 2, 3])))


main()
