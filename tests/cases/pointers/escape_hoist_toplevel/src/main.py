from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Top-level (module scope) escape: same detection should work
# outside of function bodies.
saved: Point = Point(0, 0)
for i in range(3):
    p: Point = Point(i, i)
    saved = p  # tpyc: warning(/will not keep the object it was given/)

print(saved.x, saved.y)
