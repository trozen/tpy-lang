from typing import Iterator
from tpy import Int32


def counts(n: Int32) -> Iterator[Int32]:
    i = 0
    while i < n:
        yield i
        i = i + 1
