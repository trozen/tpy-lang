from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Outer-scoped variable rebound to rvalue inside a loop. The escape
# detection doesn't flag this -- the variable's depth stays at function
# scope -- and the rebind-slot codegen keeps THIS shape correct, because
# `saved` is re-captured every iteration and so holds the last one either
# way. It is NOT safe in general: rebinding `p` after the loop overwrites
# what `saved` points at (filed in BUGS.md), which is the same single-slot
# clobber the loop-body-declared spelling is rejected for.
def rebind_gap() -> None:
    p: Point = Point(0, 0)
    saved: Point = Point(0, 0)
    for i in range(3):
        p = Point(i, i)
        saved = p
    print(saved.x, saved.y)

rebind_gap()
