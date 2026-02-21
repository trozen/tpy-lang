from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Loop-body var that is both hoisted (escapes to outer scope) and
# reassigned within the loop. Tests the intersection of hoisted_vars
# and reassigned_vars.
def hoist_and_reassign() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        p: Point = Point(i, 0)
        p = Point(i, i + 10)
        saved = p  # tpyc: warning(/hoisted to function scope/)
    print(saved.x, saved.y)

hoist_and_reassign()
