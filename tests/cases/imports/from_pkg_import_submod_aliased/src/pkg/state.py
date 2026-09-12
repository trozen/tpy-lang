from tpy import int32
from typing import Final


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


LIMIT: Final[int32] = 16
