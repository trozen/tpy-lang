from typing import Iterator
from tpy import Int32
import modb


def a(n: Int32) -> Iterator[Int32]:
    yield n
    if n > 0:
        for x in modb.b(n - 1):  # tpyc: error(/recursive generator delegation/)
            yield x
