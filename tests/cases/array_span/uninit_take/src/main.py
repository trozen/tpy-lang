# Test take/take0: move value out of storage (load + drop in one operation).
from tpy import int32
from tpy.mem import UninitArrayStorage, UninitHeapStorage

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Indexed take on heap storage
h = UninitHeapStorage[int32](3)
h.init(0, 10)
h.init(1, 20)
h.init(2, 30)
print(h.take(0))
print(h.take(1))
print(h.take(2))

# take0 shortcut on array storage
a = UninitArrayStorage[int32, 1]()
a.init0(42)
print(a.take0())

# take with record type
pts = UninitHeapStorage[Point](2)
pts.init0(Point(5, 6))
pt: Point = pts.take0()
print(pt.x)
print(pt.y)
