from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
n = 5
u: Point | int32 = n
if isinstance(u, int32):
    print(u)
