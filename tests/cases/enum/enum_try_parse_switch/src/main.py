# try_parse with 5+ members triggers switch-based dispatch
from enum import Enum
from tpy import try_parse

class Direction(Enum):
    North = 0
    South = 1
    East = 2
    West = 3
    Up = 4
    Down = 5

def try_it(name: str) -> None:
    d = try_parse(Direction, name)
    if d is not None:
        print(d)
    else:
        print("not found")

def main() -> None:
    try_it("North")
    try_it("South")
    try_it("East")
    try_it("West")
    try_it("Up")
    try_it("Down")
    try_it("Left")
    try_it("")

main()
