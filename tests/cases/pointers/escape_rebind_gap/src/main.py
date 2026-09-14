from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Outer-scoped variable rebound to rvalue inside a loop. The escape
# detection doesn't flag this -- the variable's depth stays at function
# scope -- and the rebind-slot codegen keeps THIS shape correct, because
# `saved` is re-captured every iteration and so holds the last one either
# way. A rebind of `p` after the loop while `saved` still holds the old
# object takes storage of its own (records/alias_gated_rebind pins that).
def rebind_gap() -> None:
    p: Point = Point(0, 0)
    saved: Point = Point(0, 0)
    for i in range(3):
        p = Point(i, i)
        saved = p
    print(saved.x, saved.y)

rebind_gap()
