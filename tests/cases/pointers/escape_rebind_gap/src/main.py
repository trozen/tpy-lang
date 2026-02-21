from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Outer-scoped variable rebound to rvalue inside a loop.
# The escape detection doesn't flag this (variable depth stays at function
# scope), but the rebind-slot codegen makes it safe: rvalue rebinds reuse
# the function-scoped slot instead of creating loop-scoped storage.
def rebind_gap() -> None:
    p: Point = Point(0, 0)
    saved: Point = Point(0, 0)
    for i in range(3):
        p = Point(i, i)
        saved = p
    print(saved.x, saved.y)

rebind_gap()
