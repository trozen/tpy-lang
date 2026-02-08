"""Module that exports a non-value-type global."""
from tpy import Int32


class Settings:
    width: Int32
    height: Int32

    def __init__(self, width: Int32, height: Int32) -> None:
        self.width = width
        self.height = height

    def area(self) -> Int32:
        return self.width * self.height


DEFAULT: Settings = Settings(Int32(800), Int32(600))
