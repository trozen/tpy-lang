"""Module that exports a non-value-type global."""
from tpy import int32


class Settings:
    width: int32
    height: int32

    def __init__(self, width: int32, height: int32) -> None:
        self.width = width
        self.height = height

    def area(self) -> int32:
        return self.width * self.height


DEFAULT: Settings = Settings(int32(800), int32(600))
