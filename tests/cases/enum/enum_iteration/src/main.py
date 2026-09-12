# Test enum iteration with for-each loop
from enum import Enum
from tpy import int32

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

class Status(Enum):
    Active = 1
    Inactive = 2
    Pending = 3

def print_colors() -> None:
    for c in Color:
        print(c)

def count_members() -> None:
    count: int32 = 0
    for s in Status:
        count += 1
    print(count)

def main() -> None:
    print_colors()
    count_members()

main()
