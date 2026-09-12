from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

def make_points() -> Own[list[Point]]:
    return [Point(1, 2), Point(3, 4)]

# For-each over rvalue iterable: the temporary list dies when the loop
# ends, so p's storage outlives the outer-scoped saved variable.
def rvalue_iter_escape() -> None:
    saved: Point = Point(0, 0)
    for p in make_points():
        saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
