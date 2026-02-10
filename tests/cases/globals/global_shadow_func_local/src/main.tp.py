from tpy import Int32, Bool

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Function with pointer-local `p` (branch-declared, creates rebind_slots entry)
def foo(cond: Bool) -> None:
    if cond:
        p = Point(1, 2)
    else:
        p = Point(3, 4)
    print(p.x, p.y)

# Record with method that has pointer-local `p` (tests method→global path)
class Picker:
    val: Int32
    def __init__(self, val: Int32):
        self.val = val
    def pick(self, cond: Bool) -> None:
        if cond:
            p = Point(self.val, self.val)
        else:
            p = Point(0, 0)
        print(p.x, p.y)

# Global `p` — must get its own __global_slot, not reuse stale __slot from foo/pick
p = Point(10, 20)
print(p.x, p.y)
foo(True)
Picker(99).pick(True)
