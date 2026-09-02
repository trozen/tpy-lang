from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class Q:
    y: Int32
    def __init__(self, y: Int32) -> None:
        self.y = y
for i in range(2):
    v: P | Q = P(i)
    if isinstance(v, P):
        print(v.x)
