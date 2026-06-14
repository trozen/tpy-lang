# Test UninitHeapStorage with value types (Int32) and record types (Point).
from tpy import Int32, Ptr
from tpy.unsafe import unsafe_load
from tpy.mem import UninitHeapStorage

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Value type: Int32
storage = UninitHeapStorage[Int32](4)
storage.init(0, 100)
storage.init(1, 200)
storage.init(2, 300)

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
points = UninitHeapStorage[Point](3)
points.init(0, Point(5, 6))
points.init(1, Point(7, 8))

pt: Point = points.load(0)
print(pt.x)
print(pt.y)

pt2: Point = points.load(1)
print(pt2.x)
print(pt2.y)

# load() aliases live storage (not a copy): mutating through the bound
# result is observed on a fresh load of the same slot.
pt.x = 99
print(points.load(0).x)

points.drop(0)
points.drop(1)
