# @dataclass with user-defined methods alongside auto-generated __init__
from dataclasses import dataclass
from tpy import int32

@dataclass
class Rect:
    width: int32
    height: int32

    def area(self) -> int32:
        return self.width * self.height

    def scale(self, factor: int32) -> None:
        self.width = self.width * factor
        self.height = self.height * factor

def main() -> None:
    r = Rect(3, 4)
    print(r)
    print(r.area())
    r.scale(2)
    print(r)
    print(r.area())

main()
