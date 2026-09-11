from typing import Iterator
from tpy import Int32
import moda


def b(n: Int32) -> Iterator[Int32]:
    yield n
    if n > 0:
        for x in moda.a(n - 1):
            yield x
