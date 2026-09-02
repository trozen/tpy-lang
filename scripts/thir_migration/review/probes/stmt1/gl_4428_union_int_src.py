from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
n = 5
u: Point | Int32 = n
if isinstance(u, Int32):
    print(u)
