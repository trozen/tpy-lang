from typing import ClassVar
from tpy import Int32, Own


class Counter:
    LIMIT: ClassVar[Int32] = Int32(100)
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    @staticmethod
    def make(v: Int32) -> Own["Counter"]:
        return Counter(v)
