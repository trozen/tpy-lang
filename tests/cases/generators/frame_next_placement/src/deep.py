from typing import Iterator
from tpy import int32


# main never imports this module; it reaches the frame only through relay's
# return type, so main.cpp must still include deep_inl.hpp to link.
def doubles(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i * 2
