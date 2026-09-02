from tpy import Int32, Own, copy
class Point:
    x: Int32
    def __init__(self, x: Int32):
        self.x = x
class Holder:
    value: Point | None
    def __init__(self) -> None:
        self.value = None
def make_holder() -> Own[Holder]:
    h = Holder()
    h.value = Point(1)
    return copy(h)
h = Holder()
h.value = Point(3)
g: Point | None = h.value
if g is not None:
    print(g.x)
