from enum import Enum
from typing import Self
from tpy import int32


class Shade(Enum):
    Light = 1
    Dark = 2

    def flip(self) -> Self:
        return Shade.Dark if self == Shade.Light else Shade.Light

    def weight(self) -> int32:
        return self.value * 10

    @property
    def heavy(self) -> bool:
        return self == Shade.Dark

    # generic instance and static methods
    def tag[T](self, x: T) -> T:
        return x

    @staticmethod
    def ident[T](x: T) -> T:
        return x

    @classmethod
    def parse(cls, s: str) -> Self:
        return cls[s]

    @staticmethod
    def darkest() -> "Shade":
        return Shade.Dark


def pick() -> Shade:
    return Shade.Light
