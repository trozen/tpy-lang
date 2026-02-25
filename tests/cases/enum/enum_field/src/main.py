# Enum as record field type
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

class Pixel:
    x: int
    y: int
    color: Color

    def __init__(self, x: int, y: int, color: Color) -> None:
        self.x = x
        self.y = y
        self.color = color

def main() -> None:
    p: Pixel = Pixel(0, 0, Color.Red)
    print(p.color)
    p.color = Color.Blue
    print(p.color)

main()
