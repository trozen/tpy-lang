# Warning when self.method() is called in init section RHS while fields are still uninitialized.
from tpy import int32

class Point:
    x: int32
    y: int32
    z: int32

    def magnitude(self) -> int32:
        return self.x  # simplified

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y
        self.z = self.magnitude()  # tpyc: warning(/instance method called in __init__ before all fields/)

def main() -> None:
    p = Point(int32(3), int32(4))
    print(p.z)

main()
