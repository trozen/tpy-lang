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
def probe() -> Int32:
    v = make_holder().value
    v = make_holder().value
    if v is not None:
        return v.x
    return 0
def main() -> None:
    print(probe())
main()
