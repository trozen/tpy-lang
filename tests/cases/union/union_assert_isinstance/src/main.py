# assert isinstance narrows union type for the rest of the function
from tpy import int32

class Circle:
    radius: int32
    def __init__(self, r: int32) -> None:
        self.radius = r

class Rect:
    width: int32
    def __init__(self, w: int32) -> None:
        self.width = w

def get_radius(s: Circle | Rect) -> int32:
    assert isinstance(s, Circle)
    return s.radius

def main() -> None:
    c: Circle | Rect = Circle(int32(5))
    print(get_radius(c))

main()
