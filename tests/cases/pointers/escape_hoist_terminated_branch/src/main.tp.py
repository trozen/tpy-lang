from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# The lvalue branch terminates (continue), so only the rvalue path
# reaches the escape point. Hoisting is safe.
def terminated_branch() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(99, 99)]
        p: Point = Point(i, i)
        if i == 0:
            p = items[0]
            continue
        saved = p  # tpyc: warning(/hoisted to function scope/)
    print(saved.x, saved.y)

terminated_branch()
