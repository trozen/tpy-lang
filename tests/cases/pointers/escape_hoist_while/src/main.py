from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def while_escape() -> None:
    saved: Point = Point(0, 0)
    i: Int32 = 0
    while i < 3:
        p: Point = Point(i, i)
        saved = p  # tpyc: warning(/hoisted to function scope/)
        i = i + 1
    print(saved.x, saved.y)

while_escape()
