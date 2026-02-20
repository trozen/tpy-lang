# assert isinstance on a pointer-local union variable (reassigned, so T* indirection)
from tpy import Int32

class Circle:
    radius: Int32
    def __init__(self, r: Int32) -> None:
        self.radius = r

class Rect:
    width: Int32
    def __init__(self, w: Int32) -> None:
        self.width = w

def process(flag: bool) -> Int32:
    v: Circle | Rect = Circle(Int32(1))
    v = Rect(Int32(3))  # reassignment makes v a pointer-local
    assert isinstance(v, Rect)
    return v.width

def main() -> None:
    print(process(True))

main()
