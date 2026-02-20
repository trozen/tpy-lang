# assert isinstance combined with 'and': union narrowing persists from the isinstance part
from tpy import Int32

class Circle:
    radius: Int32
    def __init__(self, r: Int32) -> None:
        self.radius = r

class Rect:
    width: Int32
    def __init__(self, w: Int32) -> None:
        self.width = w

def get_positive_radius(s: Circle | Rect) -> Int32:
    assert isinstance(s, Circle) and s.radius > Int32(0)
    return s.radius

def main() -> None:
    c: Circle | Rect = Circle(Int32(5))
    print(get_positive_radius(c))

main()
