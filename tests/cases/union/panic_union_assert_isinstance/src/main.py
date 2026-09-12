# assert isinstance panics at runtime when the union holds a different member
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
    r: Circle | Rect = Rect(int32(4))
    print(get_radius(r))

main()
