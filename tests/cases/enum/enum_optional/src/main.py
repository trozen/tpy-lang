# Optional enum (Color | None)
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def maybe_color(flag: bool) -> Color | None:
    if flag:
        return Color.Red
    return None

def main() -> None:
    c: Color | None = maybe_color(True)
    if c is not None:
        print(c)
    c = maybe_color(False)
    if c is None:
        print("no color")

main()
