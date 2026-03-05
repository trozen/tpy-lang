# Error: cannot mutate self in non-__init__ method of frozen dataclass
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True)
class Point:
    x: Int32
    y: Int32
    def move(self, dx: Int32) -> None:
        self.x = self.x + dx  # tpyc: error(/Cannot mutate readonly reference/)

def main() -> None:
    p = Point(1, 2)
    print(p)

main()
