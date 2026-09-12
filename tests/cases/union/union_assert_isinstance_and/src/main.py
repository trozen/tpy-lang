# assert isinstance combined with 'and': union narrowing persists from the isinstance part
from tpy import int32

class Circle:
    radius: int32
    def __init__(self, r: int32) -> None:
        self.radius = r

class Rect:
    width: int32
    def __init__(self, w: int32) -> None:
        self.width = w

def get_positive_radius(s: Circle | Rect) -> int32:
    assert isinstance(s, Circle) and s.radius > int32(0)
    return s.radius

def main() -> None:
    c: Circle | Rect = Circle(int32(5))
    print(get_positive_radius(c))

main()
