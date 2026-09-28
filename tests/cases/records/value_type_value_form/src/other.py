# An imported ValueType whose first default names this module's constant.
from typing import Final
from tpy import int32, ValueType

BASE: Final[int32] = 40


class Far(ValueType):
    n: int32

    def __init__(self, n: int32 = BASE) -> None:
        print("far init", n)
        self.n = n
