from tpy import Int32, Bool, copy


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


class Line:
    start: Point
    end: Point | None

    def __init__(self, s: Point):
        self.start = copy(s)
        self.end = None

    def set_end(self, e: Point) -> None:
        self.end = copy(e)

    def has_end(self) -> Bool:
        return self.end is not None

    def get_end(self) -> Point | None:
        return self.end


# Basic: init to None, check, set, access
line = Line(Point(1, 2))
print(line.has_end())
print(line.end is None)
p2 = Point(3, 4)
line.set_end(p2)
print(line.has_end())
print(line.end.x)
print(line.end.y)

# Return optional field from method (std::optional<T> → T*)
result = line.get_end()
print(result is not None)
print(result.x)

# Assign function-returned T | None into optional field (T* → std::optional<T>)
def find_point(points: list[Point], target: Int32) -> Point | None:
    for p in points:
        if p.x == target:
            return p
    return None

pts: list[Point] = list()
pts.append(Point(5, 50))
pts.append(Point(7, 70))
line2 = Line(Point(0, 0))
line2.end = find_point(pts, 5)
print(line2.end is None)
print(line2.end.x)
line2.end = find_point(pts, 7)
print(line2.end.y)
line2.end = find_point(pts, 99)
print(line2.end is None)

# Field-to-field optional assignment (std::optional<T> → std::optional<T>)
line3 = Line(Point(10, 20))
line3.end = line.end
print(line3.end.x)
print(line3.end.y)
