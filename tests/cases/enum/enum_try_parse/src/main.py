# Test try_parse() free function for safe name-to-enum conversion
from enum import Enum
from tpy import try_parse

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def try_it(name: str) -> None:
    c = try_parse(Color, name)
    if c is not None:
        print(c)
    else:
        print("not found")

def main() -> None:
    try_it("Red")
    try_it("Green")
    try_it("Blue")
    try_it("Purple")
    try_it("")

main()
