from typing import ClassVar
from tpy import int32, Own


class Widget:
    SIZE: ClassVar[int32] = int32(16)
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    @staticmethod
    def make(v: int32) -> Own["Widget"]:
        return Widget(v)
