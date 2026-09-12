from typing import Iterator
from tpy import int32
import moda


def b(n: int32) -> Iterator[int32]:
    yield n
    if n > 0:
        for x in moda.a(n - 1):
            yield x
