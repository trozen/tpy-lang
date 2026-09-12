from typing import Iterator
from tpy import int32
import modb


def a(n: int32) -> Iterator[int32]:
    yield n
    if n > 0:
        for x in modb.b(n - 1):  # tpyc: error(/recursive generator delegation/)
            yield x
