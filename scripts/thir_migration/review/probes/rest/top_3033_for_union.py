from tpy import int32
class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class Q:
    y: int32
    def __init__(self, y: int32) -> None:
        self.y = y
for i in range(2):
    v: P | Q = P(i)
    if isinstance(v, P):
        print(v.x)
