from typing import Iterator
from tpy import int32
from deep import doubles


# main imports relay, not deep: this body is inline in relay_inl.hpp and calls
# deep's __next__, so main.cpp has to include deep_inl.hpp as well.
def stream(n: int32) -> Iterator[int32]:
    for v in doubles(n):
        yield v + 1
