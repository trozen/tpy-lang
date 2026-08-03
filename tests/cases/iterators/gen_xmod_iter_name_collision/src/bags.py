# A frame-emitting iterable whose bare name collides with main.py's own Bag.
from typing import Iterator
from tpy import Int32


class Bag:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [7, 8]

    def __iter__(self) -> Iterator[Int32]:
        for x in self.items:
            yield x
            yield x
