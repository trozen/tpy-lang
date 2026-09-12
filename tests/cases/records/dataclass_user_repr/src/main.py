# @dataclass with user-defined __repr__ suppresses auto-generation
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

    def __repr__(self) -> str:
        return f"Point[{self.x},{self.y}]"

def main() -> None:
    p = Point(1, 2)
    print(p)
    print(repr(p))

main()
