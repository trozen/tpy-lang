from typing import Iterator
from tpy import int32


def counts(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i
        i = i + 1
