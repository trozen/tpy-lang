# del on record type locals
from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

def main() -> None:
    p = Point(1, 2)
    print(p.x)
    del p
    p = Point(3, 4)
    print(p.x)

main()
