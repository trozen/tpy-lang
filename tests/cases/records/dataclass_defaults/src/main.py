# @dataclass with field default values
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Color:
    r: Int32
    g: Int32
    b: Int32
    a: Int32 = 255

def main() -> None:
    red = Color(255, 0, 0)
    print(red)
    semi = Color(255, 0, 0, 128)
    print(semi)
    named = Color(r=0, g=128, b=255)
    print(named)

main()
