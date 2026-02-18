# Test UninitArrayStorage with value types (Int32) and record types (Point).
from tpy import Int32, Ptr
from tpy.unsafe import unsafe_load
from tpy.mem import UninitArrayStorage

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Value type: Int32
storage = UninitArrayStorage[Int32, 4]()
storage.init(0, 10)
storage.init(1, 20)
storage.init(2, 30)

print(storage.load(0))
print(storage.load(1))
print(storage.load(2))

# ptr() returns a raw pointer
p: Ptr[Int32] = storage.ptr()
print(unsafe_load(p, 0))

storage.drop(0)
storage.drop(1)
storage.drop(2)

# Record type: Point
points = UninitArrayStorage[Point, 3]()
points.init(0, Point(1, 2))
points.init(1, Point(3, 4))

pt: Point = points.load(0)
print(pt.x)
print(pt.y)

pt2: Point = points.load(1)
print(pt2.x)
print(pt2.y)

points.drop(0)
points.drop(1)
