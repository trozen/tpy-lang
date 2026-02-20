# assert isinstance with a message: narrowing persists after the guard
from tpy import Int32

class Circle:
    radius: Int32
    def __init__(self, r: Int32) -> None:
        self.radius = r

class Rect:
    width: Int32
    def __init__(self, w: Int32) -> None:
        self.width = w

def get_radius(s: Circle | Rect) -> Int32:
    assert isinstance(s, Circle), "expected a Circle"
    return s.radius

def main() -> None:
    c: Circle | Rect = Circle(Int32(7))
    print(get_radius(c))

main()
