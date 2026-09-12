from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Escape only happens in one branch of an if-statement inside a loop.
# The hoisted slot must still be at function scope.
def conditional_hoist() -> None:
    saved: Point = Point(0, 0)
    for i in range(5):
        p: Point = Point(i, i * 3)
        if i > 2:
            saved = p  # tpyc: warning(/will not keep the object it was given/)
    print(saved.x, saved.y)

conditional_hoist()
