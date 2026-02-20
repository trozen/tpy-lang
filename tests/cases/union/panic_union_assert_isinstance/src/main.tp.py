# assert isinstance panics at runtime when the union holds a different member
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
    assert isinstance(s, Circle)
    return s.radius

def main() -> None:
    r: Circle | Rect = Rect(Int32(4))
    print(get_radius(r))

main()
