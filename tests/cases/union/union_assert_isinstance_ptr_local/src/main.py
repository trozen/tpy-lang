# assert isinstance on a pointer-local union variable (reassigned, so T* indirection)
from tpy import int32

class Circle:
    radius: int32
    def __init__(self, r: int32) -> None:
        self.radius = r

class Rect:
    width: int32
    def __init__(self, w: int32) -> None:
        self.width = w

def process(flag: bool) -> int32:
    v: Circle | Rect = Circle(int32(1))
    v = Rect(int32(3))  # reassignment makes v a pointer-local
    assert isinstance(v, Rect)
    return v.width

def main() -> None:
    print(process(True))

main()
