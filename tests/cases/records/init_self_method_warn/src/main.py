# Warning when self.method() is called in init section RHS while fields are still uninitialized.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    z: Int32

    def magnitude(self) -> Int32:
        return self.x  # simplified

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y
        self.z = self.magnitude()  # tpyc: warning(/instance method called in __init__ before all fields/)

def main() -> None:
    p = Point(Int32(3), Int32(4))
    print(p.z)

main()
