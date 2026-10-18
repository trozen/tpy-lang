# A Final tuple named by a parameter default.
from typing import Final
from tpy import int32

PAIR: Final[tuple[int32, int32]] = (7, 2)


def ft(t: tuple[int32, int32] = PAIR, c: int32 = 0) -> int32:
    return t[0] * 10 + c
