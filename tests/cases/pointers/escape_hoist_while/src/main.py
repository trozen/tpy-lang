from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

def while_escape() -> None:
    saved: Point = Point(0, 0)
    i: int32 = 0
    while i < 3:
        p: Point = Point(i, i)
        saved = p  # tpyc: warning(/will not keep the object it was given/)
        i = i + 1
    print(saved.x, saved.y)

while_escape()
