from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
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
        saved = p  # tpyc: warning(/will not keep the object it was given/)
    print(saved.x, saved.y)

terminated_branch()
