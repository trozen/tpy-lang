from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

class Finder:
    result: Point
    def __init__(self) -> None:
        self.result = Point(0, 0)

    def find_last(self, n: Int32) -> None:
        saved: Point = Point(0, 0)
        for i in range(n):
            p: Point = Point(i, i * 2)
            saved = p  # tpyc: warning(/will not keep the object it was given/)
        self.result = saved  # tpyc: warning(/copies Point into field/)

f: Finder = Finder()
f.find_last(4)
print(f.result.x, f.result.y)
