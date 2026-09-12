# Error: cannot mutate self in non-__init__ method of frozen dataclass
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Point:
    x: int32
    y: int32
    def move(self, dx: int32) -> None:
        self.x = self.x + dx  # tpyc: error(/Cannot mutate readonly reference/)

def main() -> None:
    p = Point(1, 2)
    print(p)

main()
