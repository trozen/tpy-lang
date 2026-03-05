# @dataclass with user-defined methods alongside auto-generated __init__
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Rect:
    width: Int32
    height: Int32

    def area(self) -> Int32:
        return self.width * self.height

    def scale(self, factor: Int32) -> None:
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
