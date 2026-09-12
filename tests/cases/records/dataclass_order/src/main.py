# @dataclass(order=True) generates comparison operators via operator<=>
from dataclasses import dataclass
from tpy import int32

@dataclass(order=True)
class Point:
    x: int32
    y: int32

def main() -> None:
    a = Point(1, 2)
    b = Point(3, 4)
    c = Point(1, 3)
    # Lexicographic ordering by fields
    print(a < b)
    print(b < a)
    print(a < c)
    # <= and >=
    print(a <= Point(1, 2))
    print(a >= a)
    # > operator
    print(b > a)
    print(a > c)
    # Equality still works
    print(a == Point(1, 2))
    print(a != b)

main()
