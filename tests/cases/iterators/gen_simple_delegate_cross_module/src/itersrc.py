from typing import Iterator
from tpy import Int32


def walk() -> Iterator[Int32]:
    yield 1
    yield 2
