from tpy import Int32
from typing import Final


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


LIMIT: Final[Int32] = 16
