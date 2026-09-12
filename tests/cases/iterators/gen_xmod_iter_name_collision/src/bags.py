# A frame-emitting iterable whose bare name collides with main.py's own Bag.
from typing import Iterator
from tpy import int32


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [7, 8]

    def __iter__(self) -> Iterator[int32]:
        for x in self.items:
            yield x
            yield x
