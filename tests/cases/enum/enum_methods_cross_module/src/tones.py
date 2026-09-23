from enum import Enum


class Shade(Enum):
    Soft = 1
    Loud = 2

    def flip(self) -> str:
        return "tones:" + self.name

    @staticmethod
    def loudest() -> "Shade":
        return Shade.Loud


def pick() -> Shade:
    return Shade.Loud
