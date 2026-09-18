from typing import Iterator
from tpy import int32
import cyc_b


# A generator in an import cycle: cyc_a_inl.hpp is included after every
# complete header, so the cycle's forward-declared headers don't matter.
def countdown(n: int32) -> Iterator[int32]:
    while n > 0:
        yield cyc_b.scale(n)
        n -= 1
