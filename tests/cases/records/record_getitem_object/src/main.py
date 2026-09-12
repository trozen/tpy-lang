from tpy import int32, auto_readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

class PointList:
    data: list[Point]

    def __init__(self, pts: list[Point]) -> None:
        self.data = pts

    def __len__(self) -> int32:
        return len(self.data)

    @auto_readonly
    def __getitem__(self, index: int32) -> Point:
        return self.data[index]

def main() -> None:
    p1: Point = Point(10, 20)
    p2: Point = Point(30, 40)
    pts: list[Point] = [p1, p2]

    plist: PointList = PointList(pts)

    # Access via __getitem__ (returns const Point& in C++)
    print(plist[0].x)   # 10
    print(plist[1].y)   # 40

    # Access via operator[] (also returns const Point&)
    print(plist[-1].x)  # 30

main()
